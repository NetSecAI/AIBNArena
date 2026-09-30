"""Compiling one episode: the configuration, the scenario and its oracles.

Its own module because two callers need it and neither should import the other:
the runner, which validates before it starts anything, and the seed register,
which reads the resolved instance a seed landed on.
"""
from __future__ import annotations

from benchmarks.run import validate_bundle

from webui.catalog.experiments import ExperimentPreset
from webui.config import REPOSITORY
from webui.form import EpisodeRequest


def compile_bundle(request: EpisodeRequest, preset: ExperimentPreset):
    """The configuration, the scenario and its oracles. Touches nothing live.

    Returned whole rather than summarised because the caller may need the
    resolved instance: which device, which interface, which commands. That is
    what a seed is written down as, and what a replayed seed is held to.
    """
    return validate_bundle(
        REPOSITORY / preset.config_path,
        scenario_id=request.scenario_id,
        seed=request.seed,
        execution_budget_seconds=request.execution_budget_seconds,
        configured_model=request.model,
        seed_campaign=request.seed_campaign,
        fault_applicable=False if request.no_fault else None,
        cleanup=request.cleanup,
    )
