"""full_baseline: `langchain_agent_v2` without `get_object`.

Its path has a hyphen (`sut/step-back`), so no import statement can name this package: load it
with `importlib.import_module("sut.step-back.full_baseline")`, as its server and its tests do.
"""

from .agent import FullBaselineAgent, FullBaselineConfig

__all__ = ["FullBaselineAgent", "FullBaselineConfig"]
