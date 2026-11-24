"""dumb_baseline: `langchain_agent_v2` handed the running configuration of every node before its first turn.

Its path has a hyphen (`sut/step-back`), so no import statement can name this package: load it
with `importlib.import_module("sut.step-back.dumb_baseline")`, as its server and its tests do.
"""

from .agent import DumbBaselineAgent, DumbBaselineConfig

__all__ = ["DumbBaselineAgent", "DumbBaselineConfig"]
