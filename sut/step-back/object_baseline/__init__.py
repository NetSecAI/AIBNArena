"""object_baseline: `langchain_agent_v2` handed the topology, reading the devices through get_object only.

Its path has a hyphen (`sut/step-back`), so no import statement can name this package: load it
with `importlib.import_module("sut.step-back.object_baseline")`, as its server and its tests do.
"""

from .agent import ObjectBaselineAgent, ObjectBaselineConfig

__all__ = ["ObjectBaselineAgent", "ObjectBaselineConfig"]
