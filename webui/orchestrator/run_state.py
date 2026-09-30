"""The one run this interface allows at a time, and what the browser is told about it.

An episode deploys and mutates a real containerlab lab, so two at once would
measure each other's network. A second submission is refused while one is in
flight rather than queued: a person who asked for a run wants to know it did not
start, not find it started twenty minutes later against a lab they have moved on
from.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from webui.campaign import Cell, CampaignRequest
from webui.form import EpisodeRequest

#: How much of a run's output is kept for a browser that opens the page late.
#: An episode's two logs are on disk in full; this is only the replay buffer.
EVENT_HISTORY = 5000


class LogSource(str, Enum):
    """Which of the three voices a log line is.

    The two processes are followed side by side, so a line has to say which one
    wrote it. The launcher's own narration is neither of them: it is what this
    tool did between them -- freeing the port, proving the subject, collecting
    the result -- and putting it in either stream would read as that process
    having said it.
    """

    LAUNCHER = "launcher"
    SUT = "sut"
    BENCHMARK = "benchmark"


class RunState(str, Enum):
    VALIDATING = "validating"
    STARTING_SUT = "starting_sut"
    WAITING_READY = "waiting_ready"
    RUNNING_BENCHMARK = "running_benchmark"
    STOPPING = "stopping"
    DONE = "done"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in {RunState.DONE, RunState.FAILED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(kw_only=True)
class JobRecord:
    """What this tool runs and people watch: an id, a state, and a log.

    One episode and a whole campaign are both jobs, and they are held in one
    registry because they contend for one thing: the lab. Two registries would
    each know only their own, and `busy` would let a campaign start over an
    episode already deploying a testbed.
    """

    kind: ClassVar[str] = "job"

    id: str
    state: RunState = RunState.VALIDATING
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=EVENT_HISTORY))
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    #: The directory of a job a previous server finished (history.py): its
    #: processes' output is read back from there when its page is first opened.
    restored_from: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass(kw_only=True)
class RunRecord(JobRecord):
    """One episode."""

    kind: ClassVar[str] = "run"

    request: EpisodeRequest
    experiment_id: str
    exit_code: int | None = None
    verdict: dict[str, Any] | None = None
    result_path: str | None = None
    report_path: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            **super().summary(),
            "experiment_id": self.experiment_id,
            "exit_code": self.exit_code,
            "verdict": self.verdict,
            "result_path": self.result_path,
            "report_path": self.report_path,
            "request": self.request.model_dump(),
        }


@dataclass
class CellRecord:
    """One cell of a matrix: what it is, and how it ended.

    `state` is a string rather than a `RunState` because a cell has outcomes a
    run does not: it can be `skipped`, which is what the cells after an aborted
    campaign are -- never attempted, and not to be read as failures.
    """

    cell: dict[str, Any]
    state: str = "pending"
    verdict: dict[str, Any] | None = None
    result_path: str | None = None
    report_path: str | None = None
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            **self.cell,
            "state": self.state,
            "verdict": self.verdict,
            "result_path": self.result_path,
            "report_path": self.report_path,
            "error": self.error,
        }


@dataclass(kw_only=True)
class CampaignRecord(JobRecord):
    """A matrix of episodes, and where it is in it."""

    kind: ClassVar[str] = "campaign"

    request: CampaignRequest
    campaign_id: str
    result_dir: str
    #: The typed matrix, and `cells` its per-cell outcomes, in the same order.
    #: Both are built from one `plan()` call rather than planned twice: a page
    #: showing one order while the runner walks another would attribute each
    #: outcome to the wrong row.
    plan: list[Cell] = field(default_factory=list)
    cells: list[CellRecord] = field(default_factory=list)
    #: The campaign's own page (scripts/generate_campaign_report.py), once written.
    report_path: str | None = None
    #: One folder per model, subject and intent (scripts/export_results.py), once laid out.
    export_paths: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = {"total": len(self.cells)}
        for record in self.cells:
            tally[record.state] = tally.get(record.state, 0) + 1
        return tally

    def summary(self) -> dict[str, Any]:
        return {
            **super().summary(),
            "campaign_id": self.campaign_id,
            "result_dir": self.result_dir,
            "report_path": self.report_path,
            "export_paths": self.export_paths,
            "counts": self.counts,
            "cells": [record.summary() for record in self.cells],
            "request": self.request.model_dump(),
        }


class RunManager:
    """Holds the jobs, the one that is live, and the browsers watching them.

    One registry for episodes and campaigns both: `busy` is the lab, and a
    manager that only knew about episodes would let a campaign start on top
    of one.
    """

    def __init__(self) -> None:
        self._runs: dict[str, JobRecord] = {}
        self._order: list[str] = []
        self._current: str | None = None

    @property
    def busy(self) -> bool:
        current = self.current
        return current is not None and not current.state.finished

    @property
    def current(self) -> JobRecord | None:
        return self._runs.get(self._current) if self._current else None

    def get(self, run_id: str) -> JobRecord | None:
        return self._runs.get(run_id)

    def history(self, limit: int = 20, kind: str = RunRecord.kind) -> list[JobRecord]:
        """The most recent jobs of one kind, newest first.

        The limit counts the kind asked for, not the jobs before filtering: a
        page asking for ten episodes gets ten, however many campaigns ran
        between them.
        """
        found = []
        for key in reversed(self._order):
            record = self._runs[key]
            if record.kind != kind:
                continue
            found.append(record)
            if len(found) == limit:
                break
        return found

    def create(self, record: JobRecord) -> JobRecord:
        # No state event here: the run announces each phase as it enters it, and
        # a phase announced twice reads as a phase that ran twice.
        self._runs[record.id] = record
        self._order.append(record.id)
        self._current = record.id
        return record

    def restore(self, record: JobRecord) -> bool:
        """Put back a job a previous server finished, without making it current:
        nothing is running it, so it must not hold the lab."""
        if record.id in self._runs:
            return False
        self._runs[record.id] = record
        self._order.append(record.id)
        return True

    def reopen(self, record: JobRecord) -> None:
        """Make a finished job the live one again, at the same address.

        A resumed campaign is the same campaign: it keeps its id and its page,
        moves to the top of the history, and holds the lab while it runs.
        """
        if record.id in self._order:
            self._order.remove(record.id)
        self._runs[record.id] = record
        self._order.append(record.id)
        self._current = record.id

    def emit(self, record: JobRecord, event: dict[str, Any]) -> None:
        record.events.append(event)
        for queue in list(record.subscribers):
            queue.put_nowait(event)

    def log(self, record: JobRecord, line: str, source: LogSource) -> None:
        self.emit(record, {"type": "log", "line": line, "source": source.value})

    def set_state(self, record: JobRecord, state: RunState) -> None:
        record.state = state
        self.emit(record, {"type": "state", "state": state.value})

    def finish(self, record: JobRecord, *, error: str | None) -> None:
        record.error = error
        record.finished_at = _now()
        record.state = RunState.FAILED if error else RunState.DONE
        self.emit(record, {"type": "finished", **record.summary()})

    def subscribe(self, record: JobRecord) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        record.subscribers.append(queue)
        return queue

    def unsubscribe(self, record: JobRecord, queue: asyncio.Queue) -> None:
        if queue in record.subscribers:
            record.subscribers.remove(queue)


MANAGER = RunManager()
