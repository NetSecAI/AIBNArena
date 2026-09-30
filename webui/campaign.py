"""A matrix of episodes: what is submitted, and the order it is run in.

One campaign is subjects x models x selected scenarios x seeds. Every cell is
an ordinary `EpisodeRequest`, so nothing here re-implements what one episode is:
the same argv is built, the same flag a subject does not accept is refused, and
the same judge writes the same record. This module only decides which episodes
exist and in which order.

The order is not cosmetic. A subject's server is started once per (subject,
model, topology) and every cell sharing that triple runs against it, the way
`experiments/run_langchain_baseline.sh` does. Sorting the cells so those group
together is what keeps a forty-cell matrix from restarting a server forty times.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from webui import catalog
from webui.catalog.experiments import ExperimentPreset
from webui.form import EpisodeRequest, slug


class ScenarioSelection(BaseModel):
    """One scenario of the matrix, and whether it is the false-positive episode.

    `no_fault` is a property of the selection rather than of the scenario: the
    same scenario is often run both ways in one campaign -- once repaired,
    once on a healthy lab -- and the two are different rows here.
    """

    experiment: str
    scenario_id: str
    no_fault: bool = False
    #: The seed this row runs on, drawn at launch when it has none. It belongs
    #: to the row rather than to the campaign because a seed stands for one
    #: scenario: the register refuses the same number used for a second one, and
    #: it is right to -- two rows sharing a seed would be two different faults
    #: recorded under one name. A scenario to be run on several faults is
    #: several rows, each with its own.
    seed: int | None = None

    @property
    def key(self) -> tuple[str, str, bool, int | None]:
        return (self.experiment, self.scenario_id, self.no_fault, self.seed)


class CampaignRequest(BaseModel):
    """The matrix, fully stated, exactly as the form submits it."""

    name: str = "campaign"
    architectures: list[str] = Field(min_length=1)
    models: list[str] = Field(min_length=1)
    scenarios: list[ScenarioSelection] = Field(min_length=1)
    #: The seed of the campaign itself: one number that recalls this whole
    #: matrix. Drawn at launch, written into every record the campaign produces.
    seed_campaign: int | None = None
    execution_budget_seconds: float = Field(gt=0)
    cleanup: Literal["restore", "destroy"] | None = None
    #: The model that judges every cell's stated diagnosis against its injected
    #: fault (evaluation parameter 13); a model a registered endpoint serves, or
    #: None for diagnoses recorded unjudged. One judge for the whole matrix, so
    #: its scores are comparable across the cells.
    diagnosis_judge_model: str | None = None
    diagnosis_judge_reading: Literal["prompt_logprobs", "top_logprobs"] | None = None

    # Where each subject is served. One port for the whole matrix: the subjects
    # run one at a time, so they never need one each.
    host: str = "127.0.0.1"
    port: int | None = None

    # Settings applied to every cell. Only flags every subject accepts are
    # offered here: a matrix that refused its third subject over a flag the
    # first two took would have wasted the two runs before it.
    dry_run: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    enable_thinking: bool | None = None
    thinking_style: Literal["auto", "chat_template", "system_prompt"] | None = None
    thinking_effort: Literal["minimal", "low", "medium", "high"] | None = None
    debug_trace: Literal["off", "summary", "full"] | None = None
    ani_call_limit: int | None = None
    recursion_limit: int | None = None

    def campaign_id(self, stamp: str | None = None) -> str:
        """The directory this campaign writes under, named and dated."""
        moment = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{slug(self.name) or 'campaign'}-{moment}"


@dataclass(frozen=True)
class Cell:
    """One episode of the matrix, and where it belongs in it."""

    index: int
    architecture: str
    model: str
    selection: ScenarioSelection
    seed: int
    experiment_id: str

    def summary(self) -> dict[str, object]:
        return {
            "index": self.index,
            "architecture": self.architecture,
            "model": self.model,
            "experiment": self.selection.experiment,
            "scenario_id": self.selection.scenario_id,
            "no_fault": self.selection.no_fault,
            "seed": self.seed,
            "experiment_id": self.experiment_id,
        }


def cell_experiment_id(architecture: str, model: str, selection: ScenarioSelection,
                       seed: int) -> str:
    """What the judge records this cell as, and how its result file is found.

    The fault marker sits before the seed on purpose. `_newest_result` globs
    `{experiment_id}-*.json`, so a `...-seed9` cell would otherwise also match
    the `...-seed9-nofault-<run>.json` written by a different cell, and the two
    would be read as one.
    """
    fault = "nofault" if selection.no_fault else "fault"
    return "-".join((
        slug(architecture), slug(model), slug(selection.scenario_id), fault, f"seed{seed}",
    ))


def server_key(cell: "Cell", preset: ExperimentPreset) -> tuple[str, str, str, str]:
    """Cells sharing this key are run against one subject process.

    The topology and the healthy state are in it because a subject is started
    with the `--scenario-topology` and `--healthy-state` of the experiment it
    will be judged under; a cell from another topology needs its own server.
    Two experiments over one topology -- connectivity and qos on the small leaf
    and spine -- share a server, which the experiment key alone would not allow.
    """
    return (cell.architecture, cell.model,
            preset.topology_descriptor, preset.reference_state)


def _ordered(values: list) -> list:
    """The values as given, each kept once: a repeated seed is one cell, not two."""
    seen, result = set(), []
    for value in values:
        marker = value.key if isinstance(value, ScenarioSelection) else value
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def plan(request: CampaignRequest) -> list[Cell]:
    """Every cell of the matrix, ordered so the subject restarts as rarely as it can.

    Subject and model change slowest because changing either means a new server;
    the scenario comes last because a new experiment may mean a new topology,
    which also means a new server. One cell per row: the seed belongs to the row,
    so a scenario to be measured on several faults is several rows.
    """
    def topology_first(selection: ScenarioSelection) -> tuple[str, str, str]:
        """Sorted by what a server restart costs, not by name.

        A selection whose experiment cannot be resolved keeps its place and is
        reported by the caller's validation: ordering is not where an unknown
        experiment should be discovered.
        """
        try:
            preset = catalog.preset(selection.experiment)
        except ValueError:
            return ("", "", selection.experiment)
        return (preset.topology_descriptor, preset.reference_state, selection.experiment)

    def as_run(selection: ScenarioSelection) -> ScenarioSelection:
        """The selection as it will actually run.

        An experiment that pins `fault_applicable = false` IS the false-positive
        episode: the lifecycle skips injection whatever the row says. Recording
        such a cell as a fault episode would name it after something that did
        not happen, so the row is corrected rather than believed.
        """
        try:
            preset = catalog.preset(selection.experiment)
        except ValueError:
            return selection
        if preset.fault_applicable is False and not selection.no_fault:
            return selection.model_copy(update={"no_fault": True})
        return selection

    cells: list[Cell] = []
    for architecture in _ordered(request.architectures):
        for model in _ordered(request.models):
            for selection in map(as_run, sorted(_ordered(request.scenarios),
                                                key=topology_first)):
                # A row with no seed has not been settled yet; it is planned so
                # the matrix still has its shape, and the caller draws before
                # anything runs.
                seed = selection.seed if selection.seed is not None else 0
                cells.append(Cell(
                    index=len(cells),
                    architecture=architecture,
                    model=model,
                    selection=selection,
                    seed=seed,
                    experiment_id=cell_experiment_id(
                        architecture, model, selection, seed),
                ))
    return cells


def episode(request: CampaignRequest, cell: Cell) -> EpisodeRequest:
    """The cell as one ordinary episode.

    Everything downstream -- the argv, the flag a subject does not accept, the
    readiness check, the judge's own validation -- is the single-episode path,
    unchanged. A matrix must not be a second way of running an episode.
    """
    return EpisodeRequest(
        architecture=cell.architecture,
        experiment=cell.selection.experiment,
        scenario_id=cell.selection.scenario_id,
        model=cell.model,
        seed=cell.seed,
        seed_campaign=request.seed_campaign,
        execution_budget_seconds=request.execution_budget_seconds,
        no_fault=cell.selection.no_fault,
        cleanup=request.cleanup,
        diagnosis_judge_model=request.diagnosis_judge_model,
        diagnosis_judge_reading=request.diagnosis_judge_reading,
        host=request.host,
        port=request.port,
        dry_run=request.dry_run,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        enable_thinking=request.enable_thinking,
        thinking_style=request.thinking_style,
        thinking_effort=request.thinking_effort,
        debug_trace=request.debug_trace,
        ani_call_limit=request.ani_call_limit,
        recursion_limit=request.recursion_limit,
    )


def parse_seeds(text: str) -> list[int]:
    """`9, 17 29` as [9, 17, 29]. Raises on anything that is not a seed."""
    parts = [part for part in re.split(r"[,\s]+", text.strip()) if part]
    if not parts:
        raise ValueError("a campaign needs at least one seed")
    try:
        return [int(part) for part in parts]
    except ValueError:
        raise ValueError(f"not a list of whole numbers: {text!r}") from None
