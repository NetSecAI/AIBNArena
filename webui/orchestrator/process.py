"""Starting, streaming and stopping the processes one episode needs.

A subject is started on a port nothing listens on, and stopped after the judge
returns. A port another process holds -- a subject left from an earlier run, the
other checkout's interface, anything else -- is left to it: the next free one is
taken, and what held each busy port is named in the run log. Nothing is killed to
make room, and a listener that is not the subject just started is never judged,
since the readiness check holds the runtime document to that process id.
"""
from __future__ import annotations

import asyncio
import codecs
from contextlib import suppress
from pathlib import Path
from typing import Callable, Sequence

import psutil

LineSink = Callable[[str], None]

#: How many ports, from the one asked for, are tried before giving up.
PORT_SEARCH_LIMIT = 100


def listening_pids(port: int) -> list[int]:
    """Every process holding a listening socket on this port, on any local address.

    A listener on 0.0.0.0 conflicts with one on 127.0.0.1 even though its textual
    host differs, so the address is not compared -- only the port.
    """
    pids = set()
    for connection in psutil.net_connections(kind="inet"):
        if connection.status != psutil.CONN_LISTEN:
            continue
        if connection.laddr and connection.laddr.port == port and connection.pid:
            pids.add(connection.pid)
    return sorted(pids)


def describe(pid: int) -> str:
    try:
        process = psutil.Process(pid)
        return f"pid {pid}: {' '.join(process.cmdline()) or process.name()}"
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return f"pid {pid}: (no longer readable)"


def listening_ports() -> set[int]:
    """Every port something listens on, on any local address and for any user.

    Unlike `listening_pids`, a socket whose owner cannot be read (another user's
    process) still counts: the port is taken whoever holds it.
    """
    return {
        connection.laddr.port
        for connection in psutil.net_connections(kind="inet")
        if connection.status == psutil.CONN_LISTEN and connection.laddr
    }


def next_free_port(port: int, *, sink: LineSink, limit: int = PORT_SEARCH_LIMIT) -> int:
    """This port if nothing listens on it, else the first free one after it.

    What holds each busy port is named and left running.
    """
    busy = listening_ports()
    for candidate in range(port, min(port + limit, 65536)):
        if candidate not in busy:
            sink(f"port {candidate} is free")
            return candidate
        holders = ", ".join(describe(pid) for pid in listening_pids(candidate))
        sink(f"port {candidate} is busy ({holders or 'held by another user'}), trying {candidate + 1}")
    raise RuntimeError(f"no free port from {port} to {min(port + limit, 65536) - 1}")


class StreamedProcess:
    """A child process whose output is written to a file and to the run's log."""

    def __init__(
        self,
        name: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        sink: LineSink,
    ):
        self.name = name
        self.argv = list(argv)
        self._cwd = cwd
        self._env = env
        self._log_path = log_path
        self._sink = sink
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError(f"{self.name} has not been started")
        return self._process.pid

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self._cwd),
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader = asyncio.create_task(self._stream())

    async def _stream(self) -> None:
        """Read the child's output in chunks and split it into lines here.

        Not `async for line in stdout`: that raises once a line is longer than
        the reader's buffer, and a subject tracing a model's whole answer writes
        exactly such a line. An episode must not end because of how long a log
        line was.
        """
        assert self._process is not None and self._process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        with self._log_path.open("a", encoding="utf-8") as log:
            while True:
                chunk = await self._process.stdout.read(65536)
                if not chunk:
                    break
                *lines, pending = (pending + decoder.decode(chunk)).split("\n")
                for line in lines:
                    log.write(line + "\n")
                    self._sink(line)
                log.flush()
            if pending:
                log.write(pending + "\n")
                self._sink(pending)

    async def wait(self) -> int:
        assert self._process is not None
        if self._reader is not None:
            await self._reader
        return await self._process.wait()

    async def stop(self, *, timeout: float) -> None:
        """Stop the child through the event loop, never through `terminate`.

        `terminate` waits with psutil, and psutil reaps a child of this process
        itself (`waitpid`). The loop then never learns the child exited: uvloop,
        which uvicorn runs, waits for it forever, and the run stays "stopping".
        """
        if self._process is None or self._process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                self._process.kill()
        if self._reader is not None:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
        await self._process.wait()
