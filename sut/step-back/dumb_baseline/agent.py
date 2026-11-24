"""`langchain_agent_v2` with the running configuration of every node before its first turn.

Before the model's first call the subject itself reads the topology, then the running
configuration of every node the topology lists, each whole, and hands them to the model in
one message after the task: the model does not choose which configurations it reads. The
rest is `langchain_agent_v2`'s: the ANI tools and their text, the LangGraph loop, the rule on older
reads, the record. The prompt is v2's, word for word, `# Configuration compliance` included (since
1.1.0, 2026-11-21: until then the step-back prompts left that section out).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.common.ani_report import operation_summary
from sut.langchain_agent.ani_tools import ExecutionBudgetExceeded
from sut.langchain_agent_v2.agent import LangChainAgentV2, LangChainAgentV2Config
from sut.langchain_agent_v2.ani_tools import PlainANIAdapter

#: What the record names as the origin of the reads made before the model's first call. A
#: row with `source` is no call of the model's: the judge does not count it
#: (`benchmarks/core/sut_client.py`), and neither does this subject's `execution`.
CONFIGURATIONS_SOURCE = "configurations_before_first_turn"

#: The first line of the message that carries the configurations.
CONFIGURATIONS_HEADER = (
    "The running configuration of every node of the lab, read through the ANI before your first turn: "
    "get_running_config in text format, for every node the topology lists, each one whole.")


@dataclass(frozen=True)
class DumbBaselineConfig(LangChainAgentV2Config):
    ENV_PREFIX: ClassVar[str] = "DUMB_BASELINE"


def _failure(answer: dict[str, Any]) -> str:
    return str(answer.get("error") or "the ANI gave no reason")


def _configuration_sections(answer: dict[str, Any]) -> list[str]:
    """One section per node and path: its name, kind and command, then what the node printed,
    or why its read failed."""
    sections = []
    for node in answer.get("nodes") or []:
        for value in node.get("config") or []:
            result = value.get("result") or {}
            title = f"### {node.get('name')} ({node.get('kind')}): {result.get('command')}"
            if result.get("ok"):
                body = str(result.get("stdout") or "").rstrip("\n")
            else:
                reason = result.get("stderr") or result.get("reason") or f"return code {result.get('returncode')}"
                body = f"The read failed: {str(reason).strip()}"
            sections.append(f"{title}\n{body}")
    return sections


def read_on_own_behalf(adapter: PlainANIAdapter, operation: str, arguments: dict[str, Any],
                       source: str, category: str = "read") -> dict[str, Any]:
    """One ANI read a subject makes itself: the ANI's whole answer, filed with `source`.

    Not through a tool: no cut at `max_context_chars`, no paging, and not a failure of the
    model's in `failed_operations`, which the judge does not count for such a row.
    `category` is "validation" for a subject's own `execute_validation`.
    """
    if adapter.deadline is not None and time.monotonic() >= adapter.deadline:
        raise ExecutionBudgetExceeded(f"execution budget exhausted before the {source} reads")
    started = time.monotonic()
    try:
        raw = adapter.ani.dispatch(operation, arguments, task=adapter.task)
    except Exception as exc:  # noqa: BLE001 - answered to the model as the ANI's refusal is
        raw = {"ok": False, "operation": operation, "error": str(exc)}
    event = operation_summary(operation, raw, time.monotonic() - started, arguments)
    event.update(category=category, artifact_ref=None, source=source)
    adapter.trace.operations.append(event)
    return raw


def count_the_models_calls(report: dict[str, Any]) -> None:
    """`execution`'s counts of the model's calls only, as the judge and the shared loop count
    them: a row with `source` is the subject's, and a call refused before any tool ran is no
    read, write or validation."""
    execution = report["execution"]
    own = [item for item in report["ani_operations"] if not item.get("source")]
    performed = [item for item in own if item.get("dispatched") is not False]
    execution["tool_call_count"] = len(own)
    for key, category in (("reads", "read"), ("mutations", "mutation"), ("validations", "validation")):
        execution[key] = sum(item["category"] == category for item in performed)


class DumbBaselineANIAdapter(PlainANIAdapter):
    """v2's adapter, which also reads every node's configuration on the subject's behalf."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: What the reads before the first call gave, for the record (`DumbBaselineAgent._report`).
        self.configurations: dict[str, Any] | None = None

    def read_on_own_behalf(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One read for the configurations message (`read_on_own_behalf`)."""
        return read_on_own_behalf(self, operation, arguments, CONFIGURATIONS_SOURCE)

    def all_configurations(self) -> str:
        """The message: the running configuration of every node the topology lists."""
        started = time.monotonic()
        topology = self.read_on_own_behalf("get_topology", {})
        names = [str(node["name"]) for node in topology.get("nodes") or []
                 if isinstance(node, dict) and node.get("name")]
        if not topology.get("ok") or not names:
            body = f"get_topology failed: {_failure(topology)}. No configuration was read."
            answer: dict[str, Any] = {}
        else:
            answer = self.read_on_own_behalf("get_running_config", {"nodes": names})
            sections = _configuration_sections(answer)
            body = "\n\n".join(sections) if sections else f"get_running_config failed: {_failure(answer)}"
        message = f"{CONFIGURATIONS_HEADER}\n\n{body}"
        read = [str(node.get("name")) for node in answer.get("nodes") or []
                if all((value.get("result") or {}).get("ok") for value in node.get("config") or [])]
        self.configurations = {"nodes": names, "read": read, "chars": len(message),
                               "seconds": round(time.monotonic() - started, 3)}
        return message


class BeforeFirstTurn(AgentMiddleware):
    """Before the model's first call, one more user message after the task: what `build`
    returns, here every node's configuration (`DumbBaselineANIAdapter.all_configurations`).

    A user message, not a tool result: the rule on older reads (`KeepRecentReads`) stubs
    tool results only, so the message stays whole for the whole episode.
    """

    def __init__(self, build: Callable[[], str]) -> None:
        super().__init__()
        self.build = build

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        return {"messages": [HumanMessage(content=self.build())]}

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        # Device commands, as a tool call's are: off the event loop, so that the budget's
        # wait_for can end the episode while they run.
        return await asyncio.to_thread(self.before_agent, state, runtime)


class DumbBaselineAgent(LangChainAgentV2):
    identity = "dumb_baseline"
    #: 1.1.0 (2026-11-21, Hugo): the prompt has `# Configuration compliance`, as every repair prompt.
    version = "1.1.0"
    subject_dir = Path(__file__).resolve().parent
    adapter_class = DumbBaselineANIAdapter

    def __init__(
        self,
        config: DumbBaselineConfig | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
    ) -> None:
        super().__init__(config or DumbBaselineConfig.from_env(), ani, model=model, agent_factory=agent_factory)

    def _middleware(self, adapter: DumbBaselineANIAdapter) -> list[Any]:
        return [BeforeFirstTurn(adapter.all_configurations), *super()._middleware(adapter)]

    def _report(self, adapter: DumbBaselineANIAdapter | None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """v2's record, counting the model's calls only (`count_the_models_calls`), with what
        the reads before the first call gave."""
        report = super()._report(adapter, *args, **kwargs)
        count_the_models_calls(report)
        report["execution"]["configurations_before_first_turn"] = (
            adapter.configurations if adapter is not None else None)
        return report
