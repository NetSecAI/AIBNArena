"""object_baseline: `langchain_agent_v2` reading the devices through `get_object` only.

Before the model's first call the subject reads the lab's topology, as `get_topology_simple`
answers it, and hands it to the model in one message after the task. The model then reads the
devices itself, one object at a time: `get_running_config` and `get_state` are not offered, so
every read of a device is a `get_object` call the model makes. Since ANI v0.9 every element of a
VyOS or Arista EOS configuration belongs to some object (checked on campus_small,
docs/ani_v0_1.md). The prompt is v2's, word for word, `# Configuration compliance` included
(since 1.1.0, 2026-11-21), as in the other step-back subjects. Everything else is `langchain_agent_v2`'s.
"""
from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.langchain_agent_v2.agent import LangChainAgentV2, LangChainAgentV2Config
from sut.langchain_agent_v2.ani_tools import TOOLS as V2_TOOLS
from sut.langchain_agent_v2.ani_tools import PlainANIAdapter, simple_topology

# Its path has a hyphen (sut/step-back), which no import statement can name.
_dumb = importlib.import_module("sut.step-back.dumb_baseline.agent")
BeforeFirstTurn = _dumb.BeforeFirstTurn
count_the_models_calls = _dumb.count_the_models_calls
read_on_own_behalf = _dumb.read_on_own_behalf

#: The reads `langchain_agent_v2` offers and this subject does not: whole views of a device.
REMOVED_TOOLS = ("get_state", "get_running_config")
#: The tools the model is handed, in v2's order.
TOOLS = tuple(name for name in V2_TOOLS if name not in REMOVED_TOOLS)
#: What the record names as the origin of the topology read made before the model's first call.
#: A row with `source` is no call of the model's: the judge does not count it.
TOPOLOGY_SOURCE = "topology_before_first_turn"
#: The first line of the message that carries the topology.
TOPOLOGY_HEADER = "The lab's topology, read through the ANI before your first turn: the answer of get_topology_simple."


@dataclass(frozen=True)
class ObjectBaselineConfig(LangChainAgentV2Config):
    ENV_PREFIX: ClassVar[str] = "OBJECT_BASELINE"


class ObjectBaselineANIAdapter(PlainANIAdapter):
    """v2's adapter without the whole views of a device, which reads the topology on the
    subject's behalf."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: What the read before the first call gave, for the record (`ObjectBaselineAgent._report`).
        self.topology: dict[str, Any] | None = None

    def tools(self) -> list[BaseTool]:
        return [item for item in super().tools() if item.name not in REMOVED_TOOLS]

    def topology_message(self) -> str:
        """The message: the topology as `get_topology_simple` answers it, or why there is none."""
        started = time.monotonic()
        raw = read_on_own_behalf(self, "get_topology", {}, TOPOLOGY_SOURCE)
        answer = simple_topology(raw) if raw.get("ok") else {}
        body = (json.dumps(answer, default=str, separators=(",", ":")) if answer
                else f"get_topology_simple failed: {raw.get('error') or 'the ANI gave no reason'}")
        message = f"{TOPOLOGY_HEADER}\n\n{body}"
        self.topology = {"ok": bool(answer), "nodes": len(answer.get("nodes") or []),
                         "links": len(answer.get("links") or []), "chars": len(message),
                         "seconds": round(time.monotonic() - started, 3)}
        return message


class ObjectBaselineAgent(LangChainAgentV2):
    identity = "object_baseline"
    #: 1.1.0 (2026-11-21, Hugo): the prompt has `# Configuration compliance`, as every repair prompt.
    version = "1.1.0"
    subject_dir = Path(__file__).resolve().parent
    adapter_class = ObjectBaselineANIAdapter

    def __init__(
        self,
        config: ObjectBaselineConfig | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
    ) -> None:
        super().__init__(config or ObjectBaselineConfig.from_env(), ani, model=model, agent_factory=agent_factory)

    def _middleware(self, adapter: ObjectBaselineANIAdapter) -> list[Any]:
        return [BeforeFirstTurn(adapter.topology_message), *super()._middleware(adapter)]

    def _report(self, adapter: ObjectBaselineANIAdapter | None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """v2's record, counting the model's calls only, with what the topology read gave."""
        report = super()._report(adapter, *args, **kwargs)
        count_the_models_calls(report)
        report["execution"]["topology_before_first_turn"] = adapter.topology if adapter is not None else None
        return report
