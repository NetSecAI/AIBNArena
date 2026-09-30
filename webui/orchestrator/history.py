"""Runs outlive the server that ran them.

The registry of jobs lives in memory, so a restarted server opened on an empty
"Recent runs" table while every run's logs and result sat on disk. Each run now
leaves a run.json beside its logs, written when it starts and again when it
ends, and a starting server reads them back: the table, the run page and its
logs read as they did before the restart.

run.json holds what exists nowhere else -- the run's summary, its request and
the launcher's narration. The two processes' output is already in sut.log and
benchmark.log, so it is not copied: it is read back when the run's page is first
opened, rather than for every run each time the server starts.

A campaign already leaves its record on disk: `campaign.json`, rewritten after
every cell, holds the matrix, each cell's outcome and the campaign's links. It is
read back the same way, with the launcher's narration from `launcher.log` and the
processes' output from `judge-logs/` and `server-logs/` when its page is first
opened.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from webui.campaign import CampaignRequest
from webui.form import EpisodeRequest

from .results import repository_relative
from .run_state import (
    EVENT_HISTORY, CampaignRecord, CellRecord, JobRecord, LogSource, RunManager, RunRecord, RunState,
)

RECORD = "run.json"
#: A campaign's own record, and the launcher's narration beside it.
CAMPAIGN_RECORD = "campaign.json"
CAMPAIGN_LAUNCHER_LOG = "launcher.log"
#: What a cell's summary adds to the cell it describes.
_OUTCOME_FIELDS = ("state", "verdict", "result_path", "report_path", "error")

#: How many past runs a starting server reads back. The table shows fewer; the
#: rest stay reachable by their page until they age out of this window.
RESTORED_RUNS = 200


def save(record: RunRecord, directory: Path) -> bool:
    """Write the run's record beside its logs, replacing the last one in one step.

    Never raises for a file it cannot write: a run must not fail, and hold the
    lab, because its history could not be kept. Returns whether it was written.
    """
    document = {
        "summary": record.summary(),
        "launcher": [event["line"] for event in record.events
                     if event.get("type") == "log"
                     and event.get("source") == LogSource.LAUNCHER.value],
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".run.", suffix=".tmp")
    except OSError:
        return False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, default=str)
        os.replace(temporary, directory / RECORD)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        return False
    return True


def restore(manager: RunManager, root: Path, limit: int = RESTORED_RUNS) -> int:
    """Read the runs a previous server left under `root` back into the registry.

    Run ids begin with their UTC start time, so the directories sort oldest
    first and the registry keeps its order. A record this version cannot read is
    skipped rather than stopping the server. Returns how many were read.
    """
    restored = 0
    for path in sorted(root.glob(f"*/{RECORD}"))[-limit:]:
        try:
            record = _record(json.loads(path.read_text(encoding="utf-8")), path.parent)
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            continue
        if manager.restore(record):
            restored += 1
    return restored


def _record(document: dict[str, Any], directory: Path) -> RunRecord:
    summary = document["summary"]
    record = RunRecord(
        id=summary["id"],
        request=EpisodeRequest(**summary["request"]),
        experiment_id=summary["experiment_id"],
        state=RunState(summary["state"]),
        started_at=summary["started_at"],
        finished_at=summary.get("finished_at"),
        error=summary.get("error"),
        exit_code=summary.get("exit_code"),
        verdict=summary.get("verdict"),
        result_path=summary.get("result_path"),
        report_path=summary.get("report_path"),
        restored_from=str(directory),
    )
    if not record.state.finished:
        # The server that ran it stopped mid-run, and nothing is running it now.
        record.state = RunState.FAILED
        record.error = record.error or "the web server stopped before this run finished"
    for line in document.get("launcher") or []:
        record.events.append({"type": "log", "line": line, "source": LogSource.LAUNCHER.value})
    record.events.append({"type": "finished", **record.summary()})
    return record


def restore_campaigns(manager: RunManager, root: Path, limit: int = RESTORED_RUNS) -> int:
    """Read the campaigns a previous server left under `root` back into the registry.

    A campaign's directory is named after the campaign rather than the time it
    started, so they are ordered by the start their record gives. A record this
    version cannot read is skipped rather than stopping the server. Returns how
    many were read.
    """
    found = []
    for path in root.glob(f"*/{CAMPAIGN_RECORD}"):
        try:
            found.append(_campaign(json.loads(path.read_text(encoding="utf-8")), path.parent))
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            continue
    found.sort(key=lambda record: record.started_at)
    return sum(1 for record in found[-limit:] if manager.restore(record))


def _campaign(document: dict[str, Any], directory: Path) -> CampaignRecord:
    cells = [CellRecord(cell={key: value for key, value in cell.items() if key not in _OUTCOME_FIELDS},
                        **{key: cell.get(key) for key in _OUTCOME_FIELDS if key != "state"},
                        state=cell.get("state") or "pending")
             for cell in document["cells"]]
    record = CampaignRecord(
        # Campaigns written before their record named the page's id are reached by
        # their campaign id, which cannot collide with a run id: those begin with a date.
        id=document.get("id") or document["campaign_id"],
        request=CampaignRequest(**document["request"]),
        campaign_id=document["campaign_id"],
        result_dir=repository_relative(directory),
        cells=cells,
        state=RunState(document["state"]),
        started_at=document["started_at"],
        finished_at=document.get("finished_at"),
        error=document.get("error"),
        report_path=document.get("report_path"),
        export_paths=list(document.get("export_paths") or []),
        restored_from=str(directory),
    )
    if not record.state.finished:
        # The server that ran it stopped mid-matrix, and nothing is running it now.
        record.state = RunState.FAILED
        record.error = record.error or "the web server stopped before this campaign finished"
        for cell in record.cells:
            if cell.state == "running":
                cell.state = "failed"
                cell.error = cell.error or "interrupted: the web server stopped during this cell"
            elif cell.state == "pending":
                cell.state = "skipped"
                cell.error = cell.error or "not attempted: the web server stopped first"
    record.events.append({"type": "finished", **record.summary()})
    return record


def _process_logs(record: JobRecord, directory: Path) -> list[tuple[LogSource, list[Path]]]:
    """Where a job's two processes wrote, the judge's first.

    A campaign's are one judge log per cell and one server log per subject
    started, named after the cell it was started for; both are read in the
    matrix's order.
    """
    if isinstance(record, CampaignRecord):
        names = [f"{cell.cell.get('experiment_id')}.log" for cell in record.cells]
        return [(LogSource.BENCHMARK, [directory / "judge-logs" / name for name in names]),
                (LogSource.SUT, [directory / "server-logs" / name for name in names])]
    return [(LogSource.BENCHMARK, [directory / "benchmark.log"]), (LogSource.SUT, [directory / "sut.log"])]


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def load_logs(record: JobRecord) -> None:
    """Put a restored job's process output back into its replay, once.

    The replay holds EVENT_HISTORY events, as a live job's does. The launcher's
    lines and the final event are kept whole; the two logs share what is left,
    the judge's first since it is the shorter, each keeping its last lines.
    """
    if not record.restored_from:
        return
    directory = Path(record.restored_from)
    record.restored_from = None
    kept = list(record.events)
    launcher = [event for event in kept if event.get("type") != "finished"]
    finished = [event for event in kept if event.get("type") == "finished"]
    if isinstance(record, CampaignRecord):
        launcher += [{"type": "log", "line": line, "source": LogSource.LAUNCHER.value}
                     for line in _lines(directory / CAMPAIGN_LAUNCHER_LOG)]
    room = max(0, EVENT_HISTORY - len(launcher) - len(finished))
    output: list[dict[str, Any]] = []
    for source, paths in _process_logs(record, directory):
        lines = [line for path in paths for line in _lines(path)]
        share = room - len(output)
        tail = lines[-share:] if share > 0 else []
        output.extend({"type": "log", "line": line, "source": source.value} for line in tail)
    record.events.clear()
    record.events.extend([*launcher, *output, *finished])
