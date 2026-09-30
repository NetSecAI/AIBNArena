"""Structured lifecycle tracing and per-phase duration measurement."""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping


TraceSink = Callable[[str, Mapping[str, Any]], None]


@dataclass
class TraceRecorder:
    sink: TraceSink | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    phase_durations: dict[str, float] = field(default_factory=dict)
    active_phase: str | None = None
    failed_phase: str | None = None

    def emit(self, event: str, **payload: Any) -> None:
        item = {"event": event, **payload}
        self.events.append(item)
        if self.sink is not None:
            self.sink(event, payload)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        self.active_phase = name
        self.emit("phase_started", phase=name)
        try:
            yield
        except Exception as exc:
            self.failed_phase = name
            self.emit("phase_failed", phase=name, error=str(exc))
            raise
        finally:
            duration = time.perf_counter() - started
            self.phase_durations[name] = duration
            self.emit("phase_finished", phase=name, duration_seconds=duration)
            self.active_phase = None
