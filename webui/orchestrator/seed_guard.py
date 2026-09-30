"""Holding a seed to what it was written down as.

A seed is only worth quoting if it still means what it meant. The compiler draws
from one stream per compilation, so adding a scenario to the catalogue shifts
what every later seed lands on. Replaying such a seed would run a different
fault under the same number -- quietly, and with a record that says the two were
the same episode.

So a replayed seed is recompiled and compared against its entry before anything
is deployed, and an episode whose instance has moved is refused by name.
"""
from __future__ import annotations

from typing import Any

from webui import seeds
from webui.bundle import compile_bundle
from webui.catalog.experiments import ExperimentPreset
from webui.form import EpisodeRequest



class SeedDrift(RuntimeError):
    """A seed no longer resolves to the instance it was recorded as."""


def check(request: EpisodeRequest, preset: ExperimentPreset):
    """Compile the episode and hold its seed to the register, writing nothing.

    What validation calls. Validating a matrix must leave no trace: a seed
    recorded for a campaign somebody only previewed would name a fault that was
    never run, and the register would fill with contexts nobody can point at.
    """
    _, scenario, _ = compile_bundle(request, preset)
    known = seeds.lookup(request.seed)
    if known is not None and known[0] == "episode":
        _hold(request, known[1], scenario)
    return scenario


def settle(
    request: EpisodeRequest,
    preset: ExperimentPreset,
    *,
    run: str | None = None,
) -> dict[str, Any]:
    """Compile the episode, hold its seed to the register, and write it down.

    What a run calls. Nothing live is touched here either: this is the
    compilation the validation phase does anyway, read for what the seed
    resolved to.
    """
    scenario = check(request, preset)
    return seeds.record_episode(
        request.seed,
        experiment=request.experiment,
        scenario_id=request.scenario_id,
        domain=scenario.domain,
        topology=scenario.topology,
        testbed=preset.testbed_id,
        no_fault=request.no_fault,
        bindings=scenario.bindings,
        fault=scenario.fault,
        seed_campaign=request.seed_campaign,
        run=run,
    )


def _hold(request: EpisodeRequest, entry: dict[str, Any], scenario) -> None:
    """Refuse a seed that no longer stands for what it was written down as."""
    gaps = seeds.differences(entry, scenario.bindings, scenario.fault)
    if gaps:
        raise SeedDrift(
            f"seed {request.seed} was recorded as {entry['scenario_id']} on "
            f"{entry['experiment']} and no longer resolves to it: " + "; ".join(gaps)
        )
    if entry["scenario_id"] != request.scenario_id or entry["experiment"] != request.experiment:
        # The instance still matches, so this is a different question: the seed
        # is being reused for a context it was not written down as.
        raise SeedDrift(
            f"seed {request.seed} belongs to {entry['scenario_id']} on "
            f"{entry['experiment']}, not to {request.scenario_id} on "
            f"{request.experiment}; draw a new seed for this one"
        )


def context(seed: int) -> dict[str, Any] | None:
    """What this seed stands for, for a page asked to restore it."""
    known = seeds.lookup(seed)
    if known is None:
        return None
    kind, entry = known
    return {"kind": kind, **entry}
