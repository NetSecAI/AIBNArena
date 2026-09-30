"""The launcher's command line has to reach this subject's config.

the campaign launcher starts every subject with one command line. This
server rejected four of its flags, so the launcher skipped every group of the
subject, silently; and it read enable_thinking from an environment variable the
launcher never sets, so a thinkon run file would have produced records that claim a
condition that did not hold.
"""
from __future__ import annotations

import sys

import pytest

import sut.langchain_agent.a2a_server as a2a_server

LAUNCHER_LINE = [
    "a2a_server.py", "--host", "127.0.0.1", "--port", "8005",
    "--model", "openai/qwen3-8b-think", "--api-base", "", "--api-key", "dummy",
    "--max-execution-seconds", "1800", "--max-tokens", "8000",
    "--enable-thinking", "true", "--debug-trace", "full", "--dry-run",
]


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run main() up to the application build, and hand back the agent it built."""
    captured = {}
    monkeypatch.setattr(a2a_server, "build_a2a_application",
                        lambda agent, **kwargs: captured.setdefault("agent", agent))
    monkeypatch.setattr(a2a_server.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("IBN_SUT_TRACE_DIRECTORY", str(tmp_path))
    for name in ("LANGCHAIN_AGENT_ENABLE_THINKING", "LANGCHAIN_AGENT_DEBUG_TRACE",
                 "LANGCHAIN_AGENT_TOOL_CHOICE"):
        monkeypatch.delenv(name, raising=False)

    def run(argv):
        monkeypatch.setattr(sys, "argv", list(argv))
        a2a_server.main()
        return captured["agent"]
    return run


def test_the_launcher_command_line_reaches_the_config(started, tmp_path):
    agent = started(LAUNCHER_LINE)
    assert agent.config.enable_thinking is True
    assert agent.config.debug_trace == "full"
    assert agent.config.trace_directory == str(tmp_path)
    assert agent.config.model == "openai/qwen3-8b-think"
    assert agent.config.max_tokens == 8000


def test_without_the_flags_the_defaults_stand(started):
    agent = started(["a2a_server.py", "--api-base", "", "--model", "openai/qwen3-8b-think", "--dry-run"])
    assert agent.config.enable_thinking is None
    assert agent.config.debug_trace == "off"
    assert agent.config.tool_choice == "auto"


def test_the_tool_choice_the_environment_sets_reaches_the_config(started, monkeypatch):
    """There is no flag for it, so the environment is the only way in. Until
    2026-09-24 the server read the variable and dropped it, and bound "auto"."""
    monkeypatch.setenv("LANGCHAIN_AGENT_TOOL_CHOICE", "required")
    agent = started(["a2a_server.py", "--api-base", "", "--dry-run"])
    assert agent.config.tool_choice == "required"
