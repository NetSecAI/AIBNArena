"""The LangChain baseline with retrieval-augmented generation over `rag_document/`.

This subject *is* the baseline (`LangChainRepairAgent`): the same model binding,
ANI tools, budget, stop rules and report, inherited rather than copied, so a
difference between the two in a campaign is the retrieval and nothing else. What
it adds is a corpus of reference documents, read at startup, and two ways into it:

- `context`: before the first model turn the task is used as a query and the best
  excerpts are appended to the task message;
- `tool`: a `search_documents` tool lets the model query the corpus itself.

`rag_mode` selects one of them or `both`, the default, so a run can separate what
the injected excerpts carry from what the tool does. Everything that decides what
is retrieved (mode, ranking, chunking, and the corpus itself by digest) is recorded
in `execution.model_parameters`; what each episode retrieved is in `execution.rag`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from langchain.agents import create_agent

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent
from sut.langchain_agent.ani_tools import LangChainANIAdapter

from .document_tools import MAX_TOP_K, DocumentSearch
from .retrieval import Corpus, CorpusError, Retriever, build_retriever, load_corpus

SUBJECT_DIR = Path(__file__).resolve().parent
#: Where the documents are unless a run names another directory.
DEFAULT_RAG_DIRECTORY = SUBJECT_DIR / "rag_document"
REPOSITORY = SUBJECT_DIR.parents[1]

RAG_MODES = ("both", "context", "tool")
CONTEXT_MODES = frozenset({"both", "context"})
TOOL_MODES = frozenset({"both", "tool"})


@dataclass(frozen=True)
class LangChainRAGAgentConfig(LangChainAgentConfig):
    ENV_PREFIX: ClassVar[str] = "LANGCHAIN_RAG_AGENT"
    #: The corpus directory; None is this subject's own `rag_document/`.
    rag_directory: str | None = None
    #: A second corpus for the excerpts injected at task start (`context`); None uses
    #: the corpus above for both. Set when the task-time excerpts should come from a
    #: short troubleshooting guide while the search tool reaches the vendor manuals.
    rag_context_directory: str | None = None
    rag_mode: str = "both"
    #: Excerpts appended to the task, and returned by a search that names no count.
    rag_top_k: int = 4
    rag_chunk_chars: int = 1500
    rag_chunk_overlap: int = 200
    #: None ranks lexically (BM25); a LiteLLM embedding model id ranks by embeddings,
    #: served by the same endpoint as the chat model.
    rag_embedding_model: str | None = None

    @classmethod
    def from_env(cls) -> "LangChainRAGAgentConfig":
        prefix = cls.ENV_PREFIX
        return replace(
            super().from_env(),
            rag_directory=os.getenv(f"{prefix}_RAG_DIRECTORY") or cls.rag_directory,
            rag_context_directory=os.getenv(f"{prefix}_RAG_CONTEXT_DIRECTORY") or cls.rag_context_directory,
            rag_mode=os.getenv(f"{prefix}_RAG_MODE") or cls.rag_mode,
            rag_top_k=int(os.getenv(f"{prefix}_RAG_TOP_K") or cls.rag_top_k),
            rag_chunk_chars=int(os.getenv(f"{prefix}_RAG_CHUNK_CHARS") or cls.rag_chunk_chars),
            rag_chunk_overlap=int(os.getenv(f"{prefix}_RAG_CHUNK_OVERLAP") or cls.rag_chunk_overlap),
            rag_embedding_model=os.getenv(f"{prefix}_RAG_EMBEDDING_MODEL") or cls.rag_embedding_model,
        )


def rag_directory(config: LangChainRAGAgentConfig) -> Path:
    return Path(config.rag_directory) if config.rag_directory else DEFAULT_RAG_DIRECTORY


def _display_path(path: Path) -> str:
    """The directory as a result records it: relative to the repository when inside it,
    so two machines that ran the same corpus write the same thing."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY).as_posix()
    except ValueError:
        return str(resolved)


class LangChainRAGAgent(LangChainRepairAgent):
    """The baseline, with retrieval over a corpus of reference documents."""

    identity = "langchain_rag_agent"
    version = "1.0.0"
    subject_dir = SUBJECT_DIR

    def __init__(
        self,
        config: LangChainRAGAgentConfig | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
        retriever: Retriever | None = None,
    ) -> None:
        config = config or LangChainRAGAgentConfig.from_env()
        if config.rag_mode not in RAG_MODES:
            raise ValueError(f"unknown rag mode {config.rag_mode!r}; this subject has {list(RAG_MODES)}")
        if not 1 <= config.rag_top_k <= MAX_TOP_K:
            raise ValueError(f"rag top-k must be between 1 and {MAX_TOP_K}, not {config.rag_top_k}")
        # Read before anything else is built: a corpus that cannot be indexed stops
        # the server at startup, not the first episode of a campaign.
        self.corpus: Corpus = load_corpus(
            rag_directory(config),
            chunk_chars=config.rag_chunk_chars, chunk_overlap=config.rag_chunk_overlap)
        if not self.corpus.chunks and not config.dry_run:
            # An empty corpus would run the baseline and record a retrieval condition.
            raise CorpusError(
                f"no document to retrieve from in {self.corpus.directory}: put the reference "
                "documents there (Markdown, text, HTML or PDF) or name another directory "
                "with --rag-directory")
        self.context_corpus: Corpus | None = None
        if config.rag_context_directory:
            self.context_corpus = load_corpus(
                Path(config.rag_context_directory),
                chunk_chars=config.rag_chunk_chars, chunk_overlap=config.rag_chunk_overlap)
            if not self.context_corpus.chunks and not config.dry_run:
                raise CorpusError(
                    f"no document to retrieve from in {self.context_corpus.directory}: the "
                    "context corpus named with --rag-context-directory is empty")
        super().__init__(config, ani, model=model, agent_factory=agent_factory)
        # A dry run retrieves nothing, so it does not embed the corpus either.
        self.retriever = retriever or (None if config.dry_run else build_retriever(
            self.corpus, embedding_model=config.rag_embedding_model,
            api_base=config.api_base, api_key=config.api_key))
        # The task-time excerpts come from the context corpus when there is one; the
        # search tool always reaches the main corpus. An injected retriever (tests)
        # serves both.
        self.context_retriever: Retriever | None = None
        if self.context_corpus is not None and retriever is None and not config.dry_run:
            self.context_retriever = build_retriever(
                self.context_corpus, embedding_model=config.rag_embedding_model,
                api_base=config.api_base, api_key=config.api_key)
        self._search: DocumentSearch | None = None

    def invoke(self, task_json: str) -> str:
        # A fresh search log for every episode; the A2A executor runs one at a time.
        self._search = None
        return super().invoke(task_json)

    def _tools(self, adapter: LangChainANIAdapter) -> list[Any]:
        tools = super()._tools(adapter)
        if self.config.rag_mode in TOOL_MODES:
            tools.append(self._episode_search().tool(adapter.deadline))
        return tools

    def _task_message(self, task: dict[str, Any], deadline: float) -> str:
        message = super()._task_message(task, deadline)
        if self.config.rag_mode not in CONTEXT_MODES:
            return message
        excerpts = self._episode_search().context(task, deadline)
        return f"{message}\n\n{excerpts}" if excerpts else message

    def _report(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        report = super()._report(*args, **kwargs)
        execution = report["execution"]
        execution["model_parameters"].update(self.rag_parameters())
        execution["rag"] = self._episode_search().summary()
        return report

    def rag_parameters(self) -> dict[str, Any]:
        """Every setting that decides what is retrieved, and the corpus by digest."""
        return {
            "rag_mode": self.config.rag_mode,
            "rag_retriever": "embedding" if self.config.rag_embedding_model else "bm25",
            "rag_embedding_model": self.config.rag_embedding_model,
            "rag_top_k": self.config.rag_top_k,
            "rag_chunk_chars": self.config.rag_chunk_chars,
            "rag_chunk_overlap": self.config.rag_chunk_overlap,
            "rag_directory": _display_path(self.corpus.directory),
            "rag_corpus": self.corpus.manifest(),
            "rag_context_directory": (
                _display_path(self.context_corpus.directory) if self.context_corpus else None),
            "rag_context_corpus": self.context_corpus.manifest() if self.context_corpus else None,
        }

    def _episode_search(self) -> DocumentSearch:
        if self._search is None:
            self._search = DocumentSearch(
                self.retriever, top_k=self.config.rag_top_k,
                max_context_chars=self.config.max_context_chars, trace=self._trace,
                context_retriever=self.context_retriever)
        return self._search
