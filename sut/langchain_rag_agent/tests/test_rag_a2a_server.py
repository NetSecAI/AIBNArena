"""The launcher's command line, and this subject's own flags, reach its config."""
from __future__ import annotations

import sys

import pytest

import sut.langchain_rag_agent.a2a_server as a2a_server
from sut.langchain_rag_agent.agent import DEFAULT_RAG_DIRECTORY

LAUNCHER_LINE = [
    "a2a_server.py", "--host", "127.0.0.1", "--port", "8004",
    "--model", "openai/qwen3-8b-think", "--api-base", "", "--api-key", "dummy",
    "--max-execution-seconds", "1800", "--max-tokens", "8000",
    "--enable-thinking", "true", "--debug-trace", "full", "--dry-run",
]


@pytest.fixture
def documents(tmp_path):
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "guide.md").write_text("# Guide\n\nEnable an interface with admin-state enable.\n",
                                        encoding="utf-8")
    return directory


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run main() up to the application build, and hand back the agent it built."""
    captured = {}
    monkeypatch.setattr(a2a_server, "build_a2a_application",
                        lambda agent, **kwargs: captured.setdefault("agent", agent))
    monkeypatch.setattr(a2a_server.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("IBN_SUT_TRACE_DIRECTORY", str(tmp_path))
    for name in ("ENABLE_THINKING", "DEBUG_TRACE", "RAG_DIRECTORY", "RAG_MODE", "RAG_TOP_K",
                 "RAG_EMBEDDING_MODEL"):
        monkeypatch.delenv(f"LANGCHAIN_RAG_AGENT_{name}", raising=False)

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
    assert agent.identity == "langchain_rag_agent"


def test_the_rag_flags_reach_the_config_and_the_corpus_is_read(started, documents):
    agent = started([*LAUNCHER_LINE, "--rag-directory", str(documents), "--rag-mode", "context",
                     "--rag-top-k", "2", "--rag-chunk-chars", "600", "--rag-chunk-overlap", "50"])
    config = agent.config
    assert (config.rag_mode, config.rag_top_k, config.rag_chunk_chars, config.rag_chunk_overlap) == (
        "context", 2, 600, 50)
    assert [item.path for item in agent.corpus.files] == ["guide.md"]


def test_without_the_flags_the_defaults_stand(started):
    agent = started(["a2a_server.py", "--api-base", "", "--model", "openai/qwen3-8b-think", "--dry-run"])
    assert agent.config.rag_directory is None
    assert agent.corpus.directory == DEFAULT_RAG_DIRECTORY
    assert (agent.config.rag_mode, agent.config.rag_top_k) == ("both", 4)
    assert agent.config.rag_embedding_model is None


def test_a_corpus_that_cannot_be_indexed_stops_the_server_by_name(started, documents):
    (documents / "slides.pptx").write_bytes(b"PK")
    with pytest.raises(SystemExit, match="slides.pptx"):
        started([*LAUNCHER_LINE, "--rag-directory", str(documents)])


def test_an_empty_corpus_stops_a_live_server(started, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    # A live server needs a lab; any stands in, since the corpus is read first.
    monkeypatch.setattr(a2a_server, "build_ani", lambda *args, **kwargs: object())
    line = [item for item in LAUNCHER_LINE if item != "--dry-run"]
    with pytest.raises(SystemExit, match="no document to retrieve from"):
        started([*line, "--rag-directory", str(empty)])
