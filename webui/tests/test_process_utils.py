"""Port handling and output streaming, against throwaway processes."""
import asyncio
import os
import socket
import subprocess
import sys
import time

import pytest

from webui.orchestrator.process import StreamedProcess, listening_pids, release_port

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


def test_a_free_port_is_left_alone():
    lines = []
    release_port(free_port(), timeout=1.0, sink=lines.append)
    assert lines == [line for line in lines if "free" in line]


def test_a_stale_listener_is_stopped_and_named():
    port = free_port()
    listener = subprocess.Popen([sys.executable, "-c", LISTENER.format(port=port)])
    try:
        wait_until_listening(port)
        lines = []
        release_port(port, timeout=5.0, sink=lines.append)
        assert not listening_pids(port)
        assert listener.wait(timeout=5) is not None
        # What was killed has to be in the run log: a process this tool stops is
        # never a silent one.
        assert any(str(listener.pid) in line for line in lines)
    finally:
        if listener.poll() is None:
            listener.kill()


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


def test_a_port_held_by_something_unstoppable_is_reported(monkeypatch):
    port = free_port()
    monkeypatch.setattr("webui.orchestrator.process.listening_pids", lambda _: [1234])
    monkeypatch.setattr("webui.orchestrator.process.terminate", lambda pid, timeout: None)
    with pytest.raises(RuntimeError, match="1234"):
        release_port(port, timeout=0.1, sink=lambda _: None)


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
