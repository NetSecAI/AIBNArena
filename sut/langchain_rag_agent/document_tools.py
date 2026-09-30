"""One episode's access to the reference documentation.

Two ways in, both through `DocumentSearch.retrieve`, so both are timed, bounded by
the episode's deadline and logged the same way:

- `context`: the task itself is the query, and the excerpts found are appended to
  the first user message;
- `search_documents`: a tool the model calls with its own query.

A search is not an ANI operation: it reads no device and changes nothing, so it is
logged here and reported under `execution.rag`, never in `ani_operations`, and the
ANI counts stay comparable with the baseline's.
"""
from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from sut.common.ani_report import public_task_for_model
from sut.langchain_agent.ani_tools import ExecutionBudgetExceeded

from .retrieval import Hit, Retriever

#: The most excerpts one search may return, whoever asks.
MAX_TOP_K = 10
#: The longest query a task is reduced to; an intent is a sentence or two.
MAX_QUERY_CHARS = 2000


class SearchDocumentsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=500,
        description="What to look for, in the words the documentation would use: a command, "
                    "a feature, an attribute, an error message.")
    top_k: int | None = Field(
        default=None, ge=1, le=MAX_TOP_K,
        description="How many excerpts to return; omit for the default.")


def task_query(task: dict[str, Any]) -> str:
    """The words a task gives to search with: its intent, and what it is measured on."""
    public = public_task_for_model(task)
    parts = [public.get("intent"), public.get("question"),
             *_strings(public.get("success_criteria")), *_strings(public.get("observation"))]
    words: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip() and part.strip() not in words:
            words.append(part.strip())
    return " ".join(words)[:MAX_QUERY_CHARS]


def _strings(value: Any) -> list[str]:
    """Every string value in a JSON document, in order; keys are structure, not words."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


class DocumentSearch:
    def __init__(
        self,
        retriever: Retriever | None,
        *,
        top_k: int,
        max_context_chars: int,
        trace: Any | None = None,
        context_retriever: Retriever | None = None,
    ) -> None:
        self.retriever = retriever
        #: Answers the task-time (`context`) retrieval when set; the tool keeps `retriever`.
        self.context_retriever = context_retriever
        self.top_k = top_k
        self.max_context_chars = max_context_chars
        self.trace = trace
        self.searches: list[dict[str, Any]] = []

    def retrieve(
        self, query: str, top_k: int, *, origin: str, deadline: float | None = None,
    ) -> tuple[list[Hit], str | None]:
        """The best `top_k` excerpts for `query`, or the error that prevented it.

        The deadline is the ANI tools': past it, the episode ends on its budget as it
        would on any other call. A retriever that fails is an answer, not the end of
        the episode, and the log says so.
        """
        started = time.monotonic()
        if deadline is not None and started >= deadline:
            raise ExecutionBudgetExceeded("execution budget exhausted before document search")
        retriever = self.context_retriever if origin == "context" and self.context_retriever else self.retriever
        if retriever is None:
            raise RuntimeError("no retriever: the corpus is not indexed in dry-run mode")
        hits: list[Hit] = []
        error: str | None = None
        try:
            hits = retriever.search(
                query, top_k, timeout=None if deadline is None else deadline - started)
        except TimeoutError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.searches.append({
            "origin": origin,
            "query": query,
            "top_k": top_k,
            "seconds": round(time.monotonic() - started, 3),
            "results": [{"id": hit.chunk.id, "source": hit.chunk.source, "chunk": hit.chunk.index,
                         "score": hit.score} for hit in hits],
            "error": error,
        })
        if self.trace is not None:
            self.trace("full", "SUT document search", {
                "origin": origin, "query": query, "error": error,
                "results": [{"id": hit.chunk.id, "score": hit.score, "text": hit.chunk.text}
                            for hit in hits],
            })
        return hits, error

    def context(self, task: dict[str, Any], deadline: float | None = None) -> str | None:
        """The excerpts found for the task, as the block the first message ends with."""
        query = task_query(task)
        if not query:
            return None
        hits, _ = self.retrieve(query, self.top_k, origin="context", deadline=deadline)
        if not hits:
            return None
        header = "# Reference documentation retrieved for this task (most relevant first)"
        texts = _fit([hit.chunk.text for hit in hits], self.max_context_chars - len(header))
        blocks = [f"[{rank}] {_location(hit)}\n{text}"
                  for rank, (hit, text) in enumerate(zip(hits, texts), start=1)]
        return "\n\n".join([header, *blocks])

    def tool(self, deadline: float | None = None) -> BaseTool:
        search = self

        @tool("search_documents", args_schema=SearchDocumentsArguments)
        def search_documents(query: str, top_k: int | None = None) -> str:
            """Search the reference documentation (manuals, command references, runbooks) for how something is done: a command's syntax, a feature, a procedure. Returns ranked excerpts with their source. It reads documents, never the network."""
            hits, error = search.retrieve(query, top_k or search.top_k, origin="tool", deadline=deadline)
            if error is not None:
                return json.dumps({"ok": False, "query": query, "error": error})
            # What is left for the excerpts once each result's fields are written.
            texts = _fit([hit.chunk.text for hit in hits],
                         search.max_context_chars - 100 - 200 * len(hits))
            return json.dumps({
                "ok": True,
                "query": query,
                "results": [
                    {"rank": rank, "source": hit.chunk.source, "section": hit.chunk.section,
                     "chunk": hit.chunk.index, "score": hit.score, "text": text}
                    for rank, (hit, text) in enumerate(zip(hits, texts), start=1)
                ],
                **({} if hits else {"note": "no excerpt matched; search again with other words"}),
            }, ensure_ascii=False)

        return search_documents

    def summary(self) -> dict[str, Any]:
        """What this episode retrieved, for `execution.rag`: scores and ids, never text."""
        context = [item for item in self.searches if item["origin"] == "context"]
        return {
            "searches": [dict(item) for item in self.searches],
            "tool_searches": sum(item["origin"] == "tool" for item in self.searches),
            "context_excerpts": sum(len(item["results"]) for item in context),
        }


def _location(hit: Hit) -> str:
    return f"{hit.chunk.source} > {hit.chunk.section}" if hit.chunk.section else hit.chunk.source


def _fit(texts: list[str], budget: int) -> list[str]:
    """Cut the texts so that together they stay within `budget` characters.

    Each gets an equal share; a text shorter than its share leaves the rest to
    the others.
    """
    budget = max(budget, 200 * max(len(texts), 1))
    if sum(map(len, texts)) <= budget:
        return list(texts)
    fitted: list[str] = [""] * len(texts)
    remaining = budget
    for count, position in enumerate(sorted(range(len(texts)), key=lambda p: len(texts[p]))):
        share = remaining // (len(texts) - count)
        text = texts[position]
        fitted[position] = text if len(text) <= share else text[: max(share - 1, 0)].rstrip() + "…"
        remaining -= len(fitted[position])
    return fitted
