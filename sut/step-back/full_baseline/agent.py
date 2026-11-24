"""full_baseline: `langchain_agent_v2` without `get_object`.

The model reads the network through its whole views only (`get_topology_simple`, `get_state`,
`get_running_config`): there is no read of one named object. The prompt is v2's, word for word,
`# Configuration compliance` included (since 1.1.0, 2026-11-21), as in the other step-back subjects. Everything else is
`langchain_agent_v2`'s: the other tools and their text, the rule on older reads, the record. A
`get_object` call the model writes anyway is refused before any tool runs and filed as not
dispatched, as a call to any tool the surface lacks (`FileRefusedCalls`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from benchmarks.platforms.containerlab import ContainerLabANI
from sut.langchain_agent_v2.agent import LangChainAgentV2, LangChainAgentV2Config
from sut.langchain_agent_v2.ani_tools import TOOLS as V2_TOOLS
from sut.langchain_agent_v2.ani_tools import PlainANIAdapter

#: The tool `langchain_agent_v2` offers and this subject does not.
REMOVED_TOOL = "get_object"
#: The tools the model is handed, in v2's order.
TOOLS = tuple(name for name in V2_TOOLS if name != REMOVED_TOOL)


@dataclass(frozen=True)
class FullBaselineConfig(LangChainAgentV2Config):
    ENV_PREFIX: ClassVar[str] = "FULL_BASELINE"


class FullBaselineANIAdapter(PlainANIAdapter):
    """v2's adapter, whose surface leaves `get_object` out."""

    def tools(self) -> list[BaseTool]:
        return [item for item in super().tools() if item.name != REMOVED_TOOL]


class FullBaselineAgent(LangChainAgentV2):
    identity = "full_baseline"
    #: 1.1.0 (2026-11-21, Hugo): the prompt has `# Configuration compliance`, as every repair prompt.
    version = "1.1.0"
    subject_dir = Path(__file__).resolve().parent
    adapter_class = FullBaselineANIAdapter

    def __init__(
        self,
        config: FullBaselineConfig | None = None,
        ani: ContainerLabANI | None = None,
        *,
        model: Any | None = None,
        agent_factory: Any = create_agent,
    ) -> None:
        super().__init__(config or FullBaselineConfig.from_env(), ani, model=model, agent_factory=agent_factory)
