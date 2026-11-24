"""Port handling and output streaming, against throwaway processes."""
import asyncio
import os
import socket
import subprocess
import sys
import time

import pytest

from webui.orchestrator import process
from webui.orchestrator.process import StreamedProcess, listening_pids, next_free_port

LISTENER = (
    "import socket, time\n"
    "server = socket.socket()\n"
    "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "server.bind(('127.0.0.1', {port}))\n"
    "server.listen()\n"
    "time.sleep(120)\n"
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def two_free_ports() -> int:
    """A free port whose successor is free too."""
    for _ in range(50):
        port = free_port()
        if port < 65535 and port + 1 not in process.listening_ports():
            return port
    raise AssertionError("no two consecutive free ports")


def wait_until_listening(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if listening_pids(port):
            return
        time.sleep(0.05)
    raise AssertionError(f"nothing started listening on {port}")


def test_a_listener_is_found_by_its_port():
    port = free_port()
    with socket.socket() as server:
        server.bind(("127.0.0.1", port))
        server.listen()
        assert listening_pids(port)


def test_a_free_port_is_taken_as_asked():
    port = free_port()
    lines = []
    assert next_free_port(port, sink=lines.append) == port
    assert lines == [f"port {port} is free"]


def test_a_busy_port_is_left_to_its_holder_and_the_next_one_taken():
    port = two_free_ports()
    listener = subprocess.Popen([sys.executable, "-c", LISTENER.format(port=port)])
    try:
        wait_until_listening(port)
        lines = []
        assert next_free_port(port, sink=lines.append) == port + 1
        # Nothing is killed to make room, and what held the port is named.
        assert listener.poll() is None and listening_pids(port) == [listener.pid]
        assert str(listener.pid) in lines[0] and f"trying {port + 1}" in lines[0]
        assert lines[-1] == f"port {port + 1} is free"
    finally:
        listener.kill()
        listener.wait(timeout=5)


def test_output_is_streamed_and_logged_however_long_a_line_is(tmp_path):
    # A subject tracing a model's whole answer writes a line far past the
    # reader's default buffer; the episode must not end because of it.
    script = "print('first'); print('x' * 200000); print('last')"
    log = tmp_path / "child.log"
    lines: list[str] = []

    async def run() -> int:
        child = StreamedProcess(
            "child", [sys.executable, "-u", "-c", script],
            cwd=tmp_path, env=dict(os.environ), log_path=log, sink=lines.append)
        await child.start()
        return await child.wait()

    assert asyncio.run(run()) == 0
    assert lines[0] == "first"
    assert len(lines[1]) == 200000
    assert lines[-1] == "last"
    assert log.read_text(encoding="utf-8").splitlines() == lines


def test_a_port_another_user_holds_is_busy_all_the_same(monkeypatch):
    """Their process id cannot be read, but the port is still taken."""
    monkeypatch.setattr(process, "listening_ports", lambda: {8003})
    monkeypatch.setattr(process, "listening_pids", lambda port: [])
    lines = []
    assert next_free_port(8003, sink=lines.append) == 8004
    assert lines[0] == "port 8003 is busy (held by another user), trying 8004"


def test_a_range_with_no_free_port_is_refused(monkeypatch):
    monkeypatch.setattr(process, "listening_ports", lambda: set(range(8003, 8013)))
    monkeypatch.setattr(process, "listening_pids", lambda port: [])
    with pytest.raises(RuntimeError, match="no free port from 8003 to 8012"):
        next_free_port(8003, sink=lambda _: None, limit=10)


def test_a_subject_stops_under_the_loop_uvicorn_runs(tmp_path):
    """uvloop never learns a child exited once psutil has reaped it, so a stop
    that waited through psutil left the run "stopping" forever."""
    import uvloop

    child = ("import signal, sys, time\n"
             "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
             "print('up', flush=True)\n"
             "time.sleep(60)\n")

    async def scenario():
        process = StreamedProcess("sut", [sys.executable, "-u", "-c", child], cwd=tmp_path,
                                  env={}, log_path=tmp_path / "sut.log", sink=lambda line: None)
        await process.start()
        await asyncio.sleep(0.5)
        await asyncio.wait_for(process.stop(timeout=5), 10)
        return process.is_running()

    assert uvloop.run(scenario()) is False
