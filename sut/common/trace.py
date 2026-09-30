"""SUT-side audit trace.

The trace records what the model was asked and what it answered, which is more
than the A2A contract carries: `ani_operations` is defined as operation summaries
*without* private reasoning, and that boundary is deliberate. So the trace never
travels over A2A and is never embedded in benchmark JSON.

It can still be kept. With a directory configured, each episode appends JSON
lines to its own file and the benchmark artifact records only the path, the same
way a large ANI read is written aside and referenced. That keeps the full
interaction available for analysis without putting reasoning into the result the
scorer reads.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Tracer:
    """Callable trace sink. Levels: "off", "summary", "full".

    `directory` turns on per-episode files. `begin_episode` opens a new one; until
    it is called, entries are logged but not written, so a SUT that never starts
    an episode cannot leave a stray file.

    The episode counter lives here because it exists only to keep one run's trace
    files apart, and because a caller holding a frozen runtime cannot keep it.
    """

    level: str = "off"
    directory: str | Path | None = None
    _episodes: int = field(default=0, init=False, repr=False)
    _path: Path | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def begin_episode(self, episode_id: str) -> Path | None:
        """Start a new trace file for one episode. Returns its path, or None.

        The caller names the episode; the sequence number is appended here, so two
        episodes of the same model and scenario do not land on the same file.
        """
        self._path = None
        self._episodes += 1
        if self.level == "off" or not self.directory:
            return None
        episode_id = f"{episode_id}-{self._episodes}"
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in episode_id) or "episode"
        directory = Path(self.directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"trace-{safe}.jsonl"
            # Truncate: a re-run of the same pair replaces its trace rather than
            # appending to the previous attempt's.
            path.write_text("", encoding="utf-8")
        except OSError as exc:
            logger.warning("trace directory unusable ({}); tracing to logs only", exc)
            return None
        self._path = path
        return path

    @property
    def path(self) -> Path | None:
        return self._path

    def __call__(self, level: str, title: str, payload: Any) -> None:
        if self.level == "off":
            return
        if level == "full" and self.level != "full":
            return
        full = self.level == "full"
        logger.info("{}\n{}", title, format_trace_payload(payload, full=full))
        self._write(title, payload)

    def _write(self, title: str, payload: Any) -> None:
        if self._path is None:
            return
        # The file gets the payload whole: truncating it here would defeat the
        # reason for writing it down.
        record = {"title": title, "payload": payload}
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            line = json.dumps({"title": title, "payload": repr(payload)}, ensure_ascii=False)
        try:
            with self._lock, self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            # A failed write must not end an episode that is otherwise fine.
            logger.warning("trace write failed ({}); continuing without the file", exc)
            self._path = None


def format_trace_payload(payload: Any, *, full: bool) -> str:
    # default=str: a trace call must never be the thing that ends an episode, and
    # payloads here include provider objects that json cannot encode.
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    )
    return text if full else compact_text(text, limit=1400)


def compact_text(value: Any, limit: int = 360) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."
