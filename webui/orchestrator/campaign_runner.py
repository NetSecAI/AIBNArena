"""A matrix of episodes, run one at a time against as few subject processes as it can.

The order comes from `webui.campaign.plan`; this module walks it. Cells that
share a subject, a model and a topology are run against one server, started once
and proved once, because restarting a server between two cells that could share
it costs a minute and proves nothing.

Two rules decide what a failure means here, and they are not the same rule:

* An episode that fails is counted and the matrix carries on. A containerlab
  deployment that flaked on cell nine must not throw away cells ten to forty.
* A record whose model provenance contradicts the cell aborts the whole
  campaign. Everything after it would be attributed to a model that did not
  run, and a campaign of results nobody can trust is worse than a short one.

A campaign that stopped, or whose cells the judge could not finish, is resumed
rather than launched again: the same record walks only the cells that did not
end done, on the seeds they were planned with, so every subject of the matrix
still faces the fault the others faced.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from itertools import groupby
from pathlib import Path
from typing import Any

from webui import catalog, endpoints, seeds
from webui.argv import (
    build_benchmark_argv,
    build_benchmark_env,
    build_sut_argv,
    build_sut_env,
    resolved_port,
)
from webui.config import REPOSITORY, WebUIConfig
from webui.campaign import Cell, CampaignRequest, episode, plan, server_key

from .history import CAMPAIGN_LAUNCHER_LOG
from .process import StreamedProcess, release_port
from .readiness import wait_for_runtime_identity
from .episode_runner import diagnosis_judge
from .results import collect, provenance_mismatch, repository_relative, results_of
from .seed_guard import check, settle
from .run_state import CellRecord, CampaignRecord, LogSource, RunManager, RunState


class ProvenanceError(RuntimeError):
    """A record named a model other than the one the cell asked for."""


class ResumeRefused(ValueError):
    """A campaign that cannot be resumed, and why."""


def preflight(request: CampaignRequest, cells: list[Cell],
              *, run: str | None = None) -> None:
    """Compile every distinct cell, and hold every subject to the settings asked for.

    Nothing live is touched. A forty-cell matrix with one impossible combination
    should cost a second, not the twenty minutes it takes to reach the cell.

    Two things are checked, and the deduplication is different for each: the
    configuration and its oracles depend on the scenario, the seed and whether
    the fault is injected, while a flag a subject does not accept depends only
    on the subject. Checking each distinct case once keeps the wait short.
    """
    if cells:
        # Raises by name when the matrix names a judge no endpoint serves.
        diagnosis_judge(episode(request, cells[0]))
    for architecture_key in dict.fromkeys(cell.architecture for cell in cells):
        cell = next(item for item in cells if item.architecture == architecture_key)
        # Raises by name when a setting this subject has no flag for is set.
        build_sut_argv(
            episode(request, cell),
            catalog.architecture(architecture_key),
            catalog.preset(cell.selection.experiment),
        )

    # One settle per distinct instance. Two cells differing only by subject or
    # model are the same fault on purpose -- that is what makes them comparable
    # -- so they share one seed and one register entry.
    seen: set[tuple] = set()
    for cell in cells:
        marker = (cell.selection.key, cell.seed)
        if marker in seen:
            continue
        seen.add(marker)
        record = settle if run else check
        record(episode(request, cell), catalog.preset(cell.selection.experiment),
               **({"run": run} if run else {}))


def resume_plan(record: CampaignRecord) -> list[Cell]:
    """The cells a resumed campaign runs again: every one that did not end `done`.

    A `failed` cell is one the judge could not finish -- a lab that would not
    deploy, a record never written, a campaign stopped under it -- and a
    `skipped` one was never attempted: neither measured the subject. A subject
    that ran and did not repair ends `done` with a failing verdict and is kept;
    running it again until it passes would be choosing the result.

    The cells keep their place, their experiment id and their seed. A campaign
    read back from disk has no typed matrix, so it is planned again from the
    request it recorded and held to the cells it recorded: a catalogue that
    now plans it otherwise would pin each outcome on the wrong cell.
    """
    if not record.state.finished:
        raise ResumeRefused(f"{record.campaign_id} is still running")
    if not record.plan:
        planned = plan(record.request)
        recorded = [outcome.cell.get("experiment_id") for outcome in record.cells]
        if [cell.experiment_id for cell in planned] != recorded:
            raise ResumeRefused(
                f"{record.campaign_id} no longer plans the cells it recorded: the experiments "
                "it names have changed since it ran")
        record.plan = planned
    cells = [cell for cell in record.plan if record.cells[cell.index].state != "done"]
    if not cells:
        raise ResumeRefused(f"every cell of {record.campaign_id} is done; there is nothing to resume")
    return cells


def reopen(manager: RunManager, record: CampaignRecord, cells: list[Cell]) -> None:
    """Put a finished campaign back in flight, in place, for the cells it runs again.

    Same record, same page, same directory: the cells done keep their outcome
    and the others go back to pending. The replay loses the event that said
    the campaign ended, or a page opened now would stop reading there, and
    ends instead on those cells pending again.
    """
    for cell in cells:
        outcome = record.cells[cell.index]
        outcome.state, outcome.verdict, outcome.error = "pending", None, None
        outcome.result_path = outcome.report_path = None
    kept = [event for event in record.events if event.get("type") != "finished"]
    record.events.clear()
    record.events.extend(kept)
    record.error = record.finished_at = None
    record.state = RunState.VALIDATING
    manager.reopen(record)
    for cell in cells:
        manager.emit(record, {"type": "cell", "cell": record.cells[cell.index].summary()})


async def run_campaign(
    manager: RunManager, config: WebUIConfig, record: CampaignRecord,
    *, rerun: list[Cell] | None = None,
) -> None:
    """Walk the matrix, or only the cells `rerun` names when the campaign is resumed."""
    request = record.request
    walked = record.plan if rerun is None else rerun
    root = config.absolute(record.result_dir)
    reports = root / "reports"
    server_logs = root / "server-logs"
    judge_logs = root / "judge-logs"

    def voice(source: LogSource):
        return lambda line: manager.log(record, line, source)

    def launcher(line: str) -> None:
        # Kept on disk as well, beside the judge's and the servers' logs: it is the
        # one voice a campaign read back after a restart would otherwise lose.
        manager.log(record, line, LogSource.LAUNCHER)
        try:
            root.mkdir(parents=True, exist_ok=True)
            with (root / CAMPAIGN_LAUNCHER_LOG).open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass  # the page still has the line; a campaign does not fail over its narration

    aborted: str | None = None

    try:
        manager.set_state(record, RunState.VALIDATING)
        if rerun is None:
            launcher(f"campaign {record.campaign_id}: {len(record.plan)} cells")
        else:
            launcher(f"campaign {record.campaign_id}: resuming {len(walked)} of "
                     f"{len(record.plan)} cells, on the seeds they were planned with")
        launcher(f"seed_campaign {request.seed_campaign}: replays this whole matrix")
        await asyncio.to_thread(preflight, request, walked, run=record.id)
        launcher(f"every cell compiles; writing to {record.result_dir}")
        seeds.record_campaign(
            request.seed_campaign,
            campaign_id=record.campaign_id,
            request=request.model_dump(),
            episode_seeds=[row.seed for row in request.scenarios if row.seed is not None],
            result_dir=record.result_dir,
        )
        launcher(f"registered in {seeds.REGISTER}")
        _write_manifest(record, root)

        teardown = lab_teardown_plan(walked, lambda cell: catalog.preset(cell.selection.experiment))
        for key, group in groupby(
            walked, key=lambda cell: server_key(cell, catalog.preset(cell.selection.experiment))
        ):
            cells = list(group)
            architecture = catalog.architecture(key[0])
            model = key[1]
            port = resolved_port(episode(request, cells[0]), architecture)
            subject: StreamedProcess | None = None
            try:
                manager.set_state(record, RunState.STARTING_SUT)
                launcher(f"subject {architecture.label} on {model}: {len(cells)} cells")
                subject = await _start_subject(
                    manager, config, record, request, cells[0], architecture,
                    port=port, log_path=server_logs / f"{cells[0].experiment_id}.log",
                    launcher=launcher, sink=voice(LogSource.SUT),
                )
                for cell in cells:
                    await _run_cell(
                        manager, config, record, request, cell,
                        port=port, result_dir=root, report_dir=reports,
                        log_path=judge_logs / f"{cell.experiment_id}.log",
                        launcher=launcher, sink=voice(LogSource.BENCHMARK),
                        destroy_lab=cell.index in teardown,
                    )
                    _write_manifest(record, root)
            finally:
                if subject is not None:
                    manager.set_state(record, RunState.STOPPING)
                    await subject.stop(timeout=config.port_release_timeout_seconds)
                    launcher("subject stopped")
    except ProvenanceError as exc:
        aborted = f"campaign stopped: {exc}"
        launcher(aborted)
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the page
        aborted = f"{type(exc).__name__}: {exc}"
        launcher(aborted)

    _skip_remaining(manager, record, aborted)
    launcher(" · ".join(f"{state}: {count}" for state, count in sorted(record.counts.items())))
    # Before the page watching the campaign is told it ended: the event that closes its
    # stream is the one that can carry the links.
    await asyncio.to_thread(_write_report, record, root, aborted, launcher)
    await asyncio.to_thread(_export, record, root, config.absolute(config.export_dir), launcher)
    # A matrix whose cells failed is a finished matrix, not a failed one: the
    # failures are in the cells, where they can be read one by one. Only an
    # campaign that stopped early failed as a whole.
    manager.finish(record, error=aborted)
    # Written last so the file on disk carries the state the run ended in.
    _write_manifest(record, root)


async def _start_subject(
    manager, config, record, request, cell, architecture, *,
    port: int, log_path: Path, launcher, sink,
) -> StreamedProcess:
    """One server for every cell that can share it, proved before it is used."""
    await asyncio.to_thread(
        release_port, port, timeout=config.port_release_timeout_seconds, sink=launcher)
    episode_request = episode(request, cell)
    preset = catalog.preset(cell.selection.experiment)
    argv = [config.python, "-u", "-m", architecture.module,
            *build_sut_argv(episode_request, architecture, preset)]
    endpoint = endpoints.credentials(cell.model)
    # A model the endpoint refuses would only fail at the first model
    # call, after the testbed is deployed; asking now costs one request.
    if endpoint is not None:
        launcher(await asyncio.to_thread(endpoints.probe_model, cell.model, endpoint))
    subject = StreamedProcess(
        "sut", argv,
        cwd=REPOSITORY,
        env={**os.environ, **build_sut_env(episode_request, architecture, endpoint)},
        log_path=log_path,
        sink=sink,
    )
    launcher(f"endpoint: {endpoint.name} ({endpoint.api_base})" if endpoint
             else "endpoint: none registered for this model; the environment decides")
    launcher("starting the subject: " + " ".join(argv))
    await subject.start()

    manager.set_state(record, RunState.WAITING_READY)
    await wait_for_runtime_identity(
        episode_request.sut_url(port),
        expected={
            "schema_version": "1.0",
            "sut_identity": architecture.identity,
            "configured_model": cell.model,
            "process_id": subject.pid,
        },
        timeout=config.readiness_timeout_seconds,
        interval=config.readiness_interval_seconds,
        is_running=subject.is_running,
    )
    launcher(f"subject ready on port {port} (pid {subject.pid}, model {cell.model})")
    return subject


def lab_teardown_plan(plan, preset_of) -> set[int]:
    """The cells after which the judge destroys the lab: the last cell on each testbed.

    Cells run in plan order, grouped by subject and testbed. With `cleanup: restore`
    (the default) the judge leaves the lab deployed for the next episode, which is
    right within one testbed and wrong across them: a campaign over four testbeds
    kept four labs up at once. The last cell before the topology changes, and the
    last cell of the campaign, run with `cleanup: destroy` instead.
    """
    teardown: set[int] = set()
    cells = list(plan)
    for position, cell in enumerate(cells):
        preset = preset_of(cell)
        here = (preset.topology_descriptor, preset.reference_state)
        if position + 1 == len(cells):
            teardown.add(cell.index)
            continue
        following = preset_of(cells[position + 1])
        if (following.topology_descriptor, following.reference_state) != here:
            teardown.add(cell.index)
    return teardown


async def _run_cell(
    manager, config, record, request, cell: Cell, *,
    port: int, result_dir: Path, report_dir: Path, log_path: Path, launcher, sink,
    destroy_lab: bool = False,
) -> None:
    """One cell: the judge, its record, and what the record says about the model."""
    outcome = record.cells[cell.index]
    manager.set_state(record, RunState.RUNNING_BENCHMARK)
    _cell_state(manager, record, outcome, "running")
    launcher(f"[{cell.index + 1}/{len(record.plan)}] {cell.experiment_id}")

    episode_request = episode(request, cell)
    if destroy_lab:
        # The next cell runs on another testbed (or there is none): this one leaves
        # no lab behind. A cleanup the request already sets to destroy stays so.
        episode_request = episode_request.model_copy(update={"cleanup": "destroy"})
        launcher("last cell on this testbed: the judge destroys the lab after it")
    judge_endpoint = diagnosis_judge(episode_request)
    argv = [config.python, "-u", "benchmarks/run.py", *build_benchmark_argv(
        episode_request, catalog.preset(cell.selection.experiment),
        experiment_id=cell.experiment_id,
        sut_url=episode_request.sut_url(port),
        result_dir=str(result_dir),
        report_dir=str(report_dir),
        judge=judge_endpoint,
    )]
    judge = StreamedProcess(
        "benchmark", argv,
        cwd=REPOSITORY, env={**os.environ, **build_benchmark_env(judge_endpoint)},
        log_path=log_path, sink=sink,
    )
    # A cell run again finds its earlier attempt's record beside it: only what this
    # attempt writes is read as its outcome.
    earlier = frozenset(results_of(result_dir, cell.experiment_id))
    await judge.start()
    exit_code = await judge.wait()

    found = collect(result_dir, report_dir, cell.experiment_id, launcher, earlier=earlier)
    outcome.result_path = found["result_path"]
    outcome.verdict = found["verdict"]
    outcome.report_path = found["report_path"]

    if outcome.verdict is not None:
        # Checked here and not only by the judge: the record is what a campaign
        # is read from months later, and this is the last moment its model can
        # still be held to the one this cell asked for.
        mismatch = provenance_mismatch(outcome.verdict, cell.model)
        if mismatch:
            outcome.error = mismatch
            _cell_state(manager, record, outcome, "failed")
            raise ProvenanceError(f"{cell.experiment_id}: {mismatch}")

    if exit_code:
        outcome.error = f"the judge exited with code {exit_code}"
    elif outcome.result_path is None:
        outcome.error = "the judge wrote no result"
    _cell_state(manager, record, outcome, "failed" if outcome.error else "done")


def _cell_state(manager, record, outcome: CellRecord, state: str) -> None:
    outcome.state = state
    manager.emit(record, {"type": "cell", "cell": outcome.summary()})


def _skip_remaining(manager, record: CampaignRecord, aborted: str | None) -> None:
    """Cells the campaign never reached are skipped, never failed.

    A cell that was not attempted has measured nothing. Recording it as a
    failure would put it in the same column as a subject that ran and did not
    repair, which is the one thing a campaign must not confuse.
    """
    for outcome in record.cells:
        if outcome.state in {"pending", "running"}:
            outcome.state = "skipped"
            if aborted and outcome.error is None:
                outcome.error = "not attempted: the campaign stopped first"
            manager.emit(record, {"type": "cell", "cell": outcome.summary()})


def _manifest(record: CampaignRecord) -> dict[str, Any]:
    return {
        # The page's id, so a restarted server serves the campaign at the same address.
        "id": record.id,
        "campaign_id": record.campaign_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "state": record.state.value,
        "error": record.error,
        "counts": record.counts,
        "report_path": record.report_path,
        "export_paths": record.export_paths,
        "request": record.request.model_dump(),
        "cells": [outcome.summary() for outcome in record.cells],
    }


def _write_manifest(record: CampaignRecord, root: Path) -> None:
    """The campaign, on disk, rewritten as it advances.

    The page's own state lives in this process and is lost when it restarts;
    this file is what makes a campaign readable afterwards, next to the records
    it describes. Rewritten after every cell so an interrupted campaign still
    leaves an accurate one.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign.json").write_text(
        json.dumps(_manifest(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_report(record: CampaignRecord, root: Path, aborted: str | None, launcher) -> None:
    """The campaign's own page: the subjects compared, then every episode's interactions.

    Built from the matrix as it now stands rather than from `campaign.json`, which still
    says the campaign is running. A page that cannot be built is said and fails nothing:
    the records it would be read from are on disk, and the script builds it again.
    """
    try:
        written = _script("generate_campaign_report").write_campaign_report(
            root, manifest={**_manifest(record), "error": aborted})
    except Exception as exc:  # noqa: BLE001 - the cells are judged; the page only reads them
        launcher(f"campaign report: not written ({type(exc).__name__}: {exc})")
        return
    page = next(path for path in written if path.suffix == ".html")
    record.report_path = repository_relative(page)
    launcher(f"campaign report: {record.report_path}")


def _export(record: CampaignRecord, root: Path, out: Path, launcher) -> None:
    """Each subject's records laid out the way campaign records are shared.

    `out/<model>/<campaign>/<subject>/<intent>/`, with `records/`, `logs/`, `reports/`
    and `index.md` (scripts/export_results.py). Like the page, a layout that cannot be
    written is said and fails nothing: the records are on disk, and the script lays
    them out again.
    """
    try:
        cells = _script("export_results").export_campaign(out, root, manifest=_manifest(record))
    except Exception as exc:  # noqa: BLE001 - the cells are judged; the layout only copies them
        launcher(f"records laid out: not written ({type(exc).__name__}: {exc})")
        return
    record.export_paths = [repository_relative(Path(cell["path"])) for cell in cells]
    for path in record.export_paths:
        launcher(f"records laid out: {path}/")


def _script(name: str):
    """A script of scripts/, loaded the way benchmarks/run.py loads the episode's report
    generator (scripts/ is not a package), and only once a campaign ends."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPOSITORY / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]
