"""`langchain_agent` without what coaches the model or corrects it.

The same `create_agent` graph, model binding, ANI, record and transport: this subject is
the baseline (`LangChainRepairAgent`), as `langchain_rag_agent` is. What it takes away is
listed in its README: the send-backs, the notes and the nudge, the method and the advice
in the prompt, and the advice in the tool descriptions. An error the model makes stays
the model's, and the record shows it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.common.self_execute import DIAGNOSIS_SECONDS, _elide_older_reads, _merge_token_usage, _request_diagnosis
from sut.langchain_agent.agent import (LangChainAgentConfig, LangChainRepairAgent, SubmissionInListedOrder,
                                       _stop_after_submission)
from sut.langchain_agent.ani_tools import LangChainANIAdapter

from .ani_tools import PlainANIAdapter


@dataclass(frozen=True)
class LangChainAgentV2Config(LangChainAgentConfig):
    ENV_PREFIX: ClassVar[str] = "LANGCHAIN_AGENT_V2"
    #: The ten most recent read results stay whole, older ones become a stub naming their
    #: call, as in the shared loop. From 2026-11-15 to 2026-11-16 (main04) the v2 subjects
    #: kept instead the most recent ones up to 60 000 characters together
    #: (`self_execute.KEEP_RECENT_READ_CHARS`); set back to ten after main04 (README, "Older
    #: reads"). LANGCHAIN_AGENT_V2_KEEP_RECENT_READS=0 (or IBN_SUT_KEEP_RECENT_READS=0) turns
    #: it off; *_KEEP_RECENT_READ_CHARS sets the rule in characters, and with both a result
    #: stays whole only if each keeps it.
    keep_recent_reads: int | None = 10
    keep_recent_read_chars: int | None = None


def _as_loop_message(message: Any) -> dict[str, Any]:
    """One LangChain message in the shape the shared loop's elision reads."""
    kind = getattr(message, "type", None)
    if kind == "ai":
        return {"role": "assistant", "tool_calls": [
            {"id": call["id"], "function": {"name": call["name"], "arguments": call["args"]}}
            for call in message.tool_calls]}
    if kind == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    return {"role": kind}


class KeepRecentReads(AgentMiddleware):
    """Before each model call, every read but the most recent becomes a stub: the `keep`
    most recent, or as many as hold `keep_chars` characters together, or with both the
    reads both rules keep.

    The shared loop's own rule and its own stub (`_elide_older_reads`), so the shared loop's
    subjects and this one forget their reads alike. Only what goes to the model changes: the
    LangGraph state keeps every result whole. `events` is the record's copy of each call that elided.
    """

    def __init__(self, keep: int | None, events: list[dict[str, Any]], keep_chars: int | None = None) -> None:
        super().__init__()
        self.keep = keep
        self.keep_chars = keep_chars
        self.events = events
        self.model_calls = 0
        self.stubbed: set[int] = set()

    def _shortened(self, request: Any) -> Any:
        self.model_calls += 1
        transcript = [_as_loop_message(message) for message in request.messages]
        _elide_older_reads(transcript, set(), self.keep, keep_chars=self.keep_chars)
        messages, newly = [], []
        for index, (message, entry) in enumerate(zip(request.messages, transcript)):
            if getattr(message, "type", None) != "tool" or entry["content"] == message.content:
                messages.append(message)
                continue
            messages.append(message.model_copy(update={"content": entry["content"]}))
            if index not in self.stubbed:
                self.stubbed.add(index)
                newly.append(message)
        if newly:
            self.events.append({"turn": self.model_calls, "kind": "elided_older_reads", "count": len(newly),
                                "elided_chars": sum(len(str(message.content)) for message in newly)})
        return request.override(messages=messages)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._shortened(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._shortened(request))


class RememberTranscript(AgentMiddleware):
    """Keeps what the last model call was sent, for the diagnosis turn of an episode with no
    conclusion. A reply that called a tool is left out: its results may never have come, and
    a transcript ending on it could not be sent again."""

    def __init__(self, adapter: PlainANIAdapter) -> None:
        super().__init__()
        self.adapter = adapter

    def _remember(self, request: Any, response: Any) -> Any:
        replies = [message for message in (getattr(response, "result", None) or [])
                   if not (getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None))]
        self.adapter.transcript = [*request.messages, *replies]
        return response

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        self.adapter.transcript = list(request.messages)
        return self._remember(request, handler(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        self.adapter.transcript = list(request.messages)
        return self._remember(request, await handler(request))


class FileRefusedCalls(AgentMiddleware):
    """A tool call `create_agent` answers with an error before any tool runs is filed in the record.

    Its ToolNode answers so a call whose arguments the tool's model refuses and a name no tool
    has; the tool's own code, which files every other call (`_invoke`), never runs. The shared
    loop files such a call as an operation that was not dispatched, so this one does
    too (`PlainANIAdapter.file_refusal`), and the judge counts the same error alike in both.
    """

    def __init__(self, adapter: PlainANIAdapter) -> None:
        super().__init__()
        self.adapter = adapter

    def _file(self, request: Any, response: Any) -> Any:
        if isinstance(response, ToolMessage) and response.status == "error":
            self.adapter.file_refusal(request.tool_call, str(response.content))
        return response

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self._file(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self._file(request, await handler(request))


class LangChainAgentV2(LangChainRepairAgent):
    identity = "langchain_agent_v2"
    version = "2.0.0"
    subject_dir = Path(__file__).resolve().parent
    adapter_class = PlainANIAdapter

    def __init__(
        self,
        config: LangChainAgentV2Config | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
    ) -> None:
        super().__init__(config or LangChainAgentV2Config.from_env(), ani, model=model, agent_factory=agent_factory)

    def _middleware(self, adapter: LangChainANIAdapter) -> list[Any]:
        """What ends the episode, the rule on older reads, and the record of a call refused
        before any tool ran. A reply with no tool call ends the graph, as `create_agent` does,
        and the record names it `no_conclusion`."""
        keep, keep_chars = self.config.keep_recent_reads, self.config.keep_recent_read_chars
        window = [KeepRecentReads(keep, adapter.elided_reads, keep_chars)] if keep or keep_chars else []
        # In this order: the first wraps the others, so the transcript kept is the one sent.
        return [_stop_after_submission(adapter), *window, RememberTranscript(adapter), FileRefusedCalls(adapter),
                SubmissionInListedOrder()]

    def _report(self, adapter: LangChainANIAdapter | None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """The baseline's record, with what the shared loop records of its older reads."""
        report = super()._report(adapter, *args, **kwargs)
        events = list(adapter.elided_reads) if adapter is not None else []
        execution = report["execution"]
        # A call refused before any tool ran (`FileRefusedCalls`) is an attempt, and no read,
        # write or validation: the judge counts it so (`dispatched: false`), and so do these.
        performed = [item for item in report["ani_operations"] if item.get("dispatched") is not False]
        for key, category in (("reads", "read"), ("mutations", "mutation"), ("validations", "validation")):
            execution[key] = sum(item["category"] == category for item in performed)
        execution["successful_mutations"] = sum(item["category"] == "mutation" and item.get("ok") is True
                                                for item in performed)
        execution["model_parameters"]["keep_recent_reads"] = self.config.keep_recent_reads
        execution["model_parameters"]["keep_recent_read_chars"] = self.config.keep_recent_read_chars
        execution["context"] = {"keep_recent_reads": self.config.keep_recent_reads,
                                "keep_recent_read_chars": self.config.keep_recent_read_chars,
                                "elided_results": sum(event["count"] for event in events), "events": events}
        if adapter is not None and report["final_response"] is None:
            report["diagnosis"] = self._final_diagnosis(adapter, execution)
        return report

    def _final_diagnosis(self, adapter: PlainANIAdapter, execution: dict[str, Any]) -> dict[str, Any]:
        """The fault as the subject states it in one tool-less turn, after an episode with no
        conclusion: the shared loop's own turn (`_request_diagnosis`) and record, so the
        diagnosis of both v2 subjects is measured alike. Nothing else in the record changes."""
        cause = (execution.get("termination") or {}).get("cause")
        if cause in {"context_budget", "context_window"} or not adapter.transcript:
            reason = (f"the episode ended on {cause}, so the transcript cannot be sent again"
                      if adapter.transcript else "no model call was made")
            return {"text": None, "source": "termination_turn", "requested": False, "error": f"not asked: {reason}"}
        model = self._model(request_timeout=DIAGNOSIS_SECONDS).bind_tools(self._tools(adapter))
        usage: list[dict[str, Any]] = []

        def call_model(transcript: list[Any], _remaining: float) -> Any:
            reply = model.invoke(transcript)
            usage.append(getattr(reply, "usage_metadata", None) or {})
            return reply

        diagnosis = _request_diagnosis(SimpleNamespace(call_model=call_model, trace=self._trace),
                                       [SystemMessage(content=self.system_prompt), *adapter.transcript])
        diagnosis.pop("_token_usage", None)
        for item in usage:
            _merge_token_usage(execution["token_usage"], item)
        return diagnosis
