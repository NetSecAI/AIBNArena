"""Every subject server must compile and parse its own command line.

The suite parses the servers with ast (test_subject_identity), which accepts a
repeated keyword argument that the interpreter rejects, and nothing invoked their
argument parsers; a duplicated ``--artifact-directory`` and a duplicated keyword
in sut/langchain_agent/a2a_server.py therefore reached main while every test was
green, and that subject could not be started at all. Compiled and asked for
``--help`` here, which is where argparse raises on a conflicting option.
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVERS = sorted(ROOT.glob("sut/**/a2a_server.py"))


@pytest.mark.parametrize("server", SERVERS, ids=[str(p.relative_to(ROOT)) for p in SERVERS])
def test_server_compiles(server: Path):
    py_compile.compile(str(server), doraise=True)


@pytest.mark.parametrize("server", SERVERS, ids=[str(p.relative_to(ROOT)) for p in SERVERS])
def test_server_parses_its_command_line(server: Path):
    completed = subprocess.run(
        [sys.executable, str(server), "--help"], cwd=ROOT,
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-2000:]


#: What the campaign launcher passes to every subject it starts. Written
#: out here because the launcher is not tracked: this test is the contract. A server
#: that rejects one of these is skipped by the launcher, group after group, with
#: nothing in the results to say so.
LAUNCHER_FLAGS = (
    "--host", "--port", "--model", "--api-base", "--api-key", "--max-execution-seconds",
    "--max-tokens", "--scenario-topology", "--healthy-state",
    "--prompt-variant", "--ani-call-limit", "--enable-thinking", "--min-retry-tokens", "--context-budget-chars", "--tool-result-chars",
    "--artifact-directory", "--max-consecutive-rejections", "--debug-trace",
)


@pytest.mark.parametrize("server", SERVERS, ids=[str(p.relative_to(ROOT)) for p in SERVERS])
def test_server_accepts_the_launcher_command_line(server: Path):
    completed = subprocess.run(
        [sys.executable, str(server), "--help"], cwd=ROOT,
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-2000:]
    missing = [flag for flag in LAUNCHER_FLAGS if flag not in completed.stdout]
    assert not missing, f"{server.relative_to(ROOT)} does not accept {missing}"
