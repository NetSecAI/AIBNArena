"""The RAG subject is the baseline plus retrieval, and says what it retrieved."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sut.common.tests.test_recorded_parameters import REQUIRED
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent, StructuredConclusion
from sut.langchain_rag_agent import LangChainRAGAgent, LangChainRAGAgentConfig
from sut.langchain_rag_agent.agent import DEFAULT_RAG_DIRECTORY
from sut.langchain_rag_agent.retrieval import CorpusError

ANI_TOOLS = ["get_topology", "get_state", "get_running_config", "get_object",
             "update_config", "update_object", "execute_validation", "rollback_config"]

TASK = {
    "intent": "Restore the intended IPv4 connectivity: an interface was administratively disabled.",
    "execution_budget": {"wall_clock_seconds": 5},
    "success_criteria": {"all_of": [{"type": "lab_connectivity"}]},
}

ADMIN_STATE = """# SR Linux interfaces

## Admin state

An interface that was administratively disabled is enabled again with
`set / interface ethernet-1/1 admin-state enable`.
"""

QOS = """# QoS

## Shaping

Traffic shaping on VyOS uses `set qos policy shaper`.
"""


class FakeANI:
    def dispatch(self, operation, arguments, *, task=None):
        if operation == "update_config":
            return {"ok": True, "operation": operation, "transaction_id": "tx1",
                    "changes": [{"target": "leaf1", "commands": ["x"], "result": {"ok": True, "safe": True}}]}
        if operation == "execute_validation":
            return {"ok": True, "operation": operation,
                    "checks": [{"type": "public_success_criteria", "passed": True}]}
        return {"ok": True, "operation": operation}


class Graph:
    """Stands in for create_agent's graph: runs a script over the tools it was given."""

    def __init__(self, tools, script):
        self.tools = {item.name: item for item in tools}
        self.script = script
        self.messages = None

    async def ainvoke(self, state, config=None):
        self.messages = state["messages"]
        return self.script(self.tools)


def repaired(tools):
    tools["update_config"].invoke({"changes": [{"target": "leaf1", "commands": ["x"]}]})
    tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
    return {"structured_response": StructuredConclusion(status="completed", summary="validated")}


@pytest.fixture
def documents(tmp_path: Path) -> Path:
    directory = tmp_path / "rag_document"
    directory.mkdir()
    (directory / "srlinux.md").write_text(ADMIN_STATE, encoding="utf-8")
    (directory / "qos.md").write_text(QOS, encoding="utf-8")
    return directory


def build(documents: Path, tmp_path: Path, script=repaired, *, retriever=None, **settings):
    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        captured["graph"] = Graph(kwargs["tools"], script)
        return captured["graph"]

    config = LangChainRAGAgentConfig(
        rag_directory=str(documents), artifact_directory=str(tmp_path / "artifacts"), **settings)
    agent = LangChainRAGAgent(config, ani=FakeANI(), model=object(), agent_factory=factory,
                              retriever=retriever)
    return agent, captured


def run(agent, task=TASK):
    return json.loads(agent.invoke(json.dumps(task)))


# --- what the model is given -------------------------------------------------------

def test_both_the_excerpts_and_the_search_tool_are_given_by_default(documents, tmp_path):
    agent, captured = build(documents, tmp_path)
    report = run(agent)
    assert [tool.name for tool in captured["tools"]] == [*ANI_TOOLS, "search_documents"]
    message = captured["graph"].messages[0]["content"]
    task_json, excerpts = message.split("\n\n", 1)
    assert json.loads(task_json)["intent"] == TASK["intent"], "the task comes first, as the baseline sends it"
    assert excerpts.startswith("# Reference documentation retrieved for this task")
    assert "[1] srlinux.md > SR Linux interfaces > Admin state" in excerpts
    assert "admin-state enable" in excerpts
    assert report["status"] == "completed" and report["verified"] is True
    assert report["sut_identity"] == "langchain_rag_agent"


def test_context_mode_gives_no_tool(documents, tmp_path):
    agent, captured = build(documents, tmp_path, rag_mode="context")
    run(agent)
    assert [tool.name for tool in captured["tools"]] == ANI_TOOLS
    assert "admin-state enable" in captured["graph"].messages[0]["content"]


def test_tool_mode_sends_the_baselines_message_unchanged(documents, tmp_path):
    agent, captured = build(documents, tmp_path, rag_mode="tool")
    run(agent)
    assert "search_documents" in [tool.name for tool in captured["tools"]]

    baseline_captured: dict = {}

    def factory(**kwargs):
        baseline_captured["graph"] = Graph(kwargs["tools"], repaired)
        return baseline_captured["graph"]

    baseline = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)),
                                    ani=FakeANI(), model=object(), agent_factory=factory)
    baseline.invoke(json.dumps(TASK))
    assert captured["graph"].messages == baseline_captured["graph"].messages


def test_a_search_answers_with_ranked_excerpts_and_is_not_an_ani_operation(documents, tmp_path):
    answers = []

    def script(tools):
        answers.append(json.loads(tools["search_documents"].invoke({"query": "shaper qos policy"})))
        answers.append(json.loads(tools["search_documents"].invoke({"query": "zzz", "top_k": 2})))
        return repaired(tools)

    agent, _ = build(documents, tmp_path, script, rag_mode="tool")
    report = run(agent)
    found, nothing = answers
    assert found["ok"] is True
    assert found["results"][0]["source"] == "qos.md"
    assert found["results"][0]["section"] == "QoS > Shaping"
    assert "set qos policy shaper" in found["results"][0]["text"]
    assert nothing["results"] == [] and "other words" in nothing["note"]
    # Searches are counted apart: the ANI counts are the baseline's.
    assert report["execution"]["tool_call_count"] == 2
    assert [item["operation"] for item in report["ani_operations"]] == ["update_config", "execute_validation"]
    rag = report["execution"]["rag"]
    assert rag["tool_searches"] == 2 and rag["context_excerpts"] == 0
    assert [item["query"] for item in rag["searches"]] == ["shaper qos policy", "zzz"]
    assert rag["searches"][0]["results"][0]["id"] == "qos.md#0"
    assert "text" not in rag["searches"][0]["results"][0], "the report names excerpts, the trace holds them"


def test_the_context_search_is_recorded(documents, tmp_path):
    agent, _ = build(documents, tmp_path)
    rag = run(agent)["execution"]["rag"]
    assert rag["searches"][0]["origin"] == "context"
    assert rag["searches"][0]["query"].startswith(TASK["intent"])
    assert "lab_connectivity" in rag["searches"][0]["query"]
    assert rag["context_excerpts"] == len(rag["searches"][0]["results"]) > 0


def test_each_episode_starts_a_new_search_log(documents, tmp_path):
    agent, _ = build(documents, tmp_path)
    run(agent)
    assert len(run(agent)["execution"]["rag"]["searches"]) == 1


# --- what goes wrong -------------------------------------------------------------

class BrokenRetriever:
    name = "broken"

    def search(self, query, top_k, *, timeout=None):
        raise ConnectionError("embedding endpoint unreachable")


def test_a_failed_search_is_an_answer_and_is_recorded(documents, tmp_path):
    answers = []

    def script(tools):
        answers.append(json.loads(tools["search_documents"].invoke({"query": "mtu"})))
        return repaired(tools)

    agent, captured = build(documents, tmp_path, script, retriever=BrokenRetriever())
    report = run(agent)
    assert answers[0]["ok"] is False and "unreachable" in answers[0]["error"]
    assert report["status"] == "completed", "the episode went on"
    assert json.loads(captured["graph"].messages[0]["content"]) == json.loads(json.dumps(
        {key: TASK[key] for key in sorted(TASK)})), "no excerpts when the context search failed"
    errors = [item["error"] for item in report["execution"]["rag"]["searches"]]
    assert all("unreachable" in error for error in errors) and len(errors) == 2


def test_a_search_past_the_deadline_ends_the_episode_on_its_budget(documents, tmp_path):
    def script(tools):
        time.sleep(0.05)
        tools["search_documents"].invoke({"query": "mtu"})
        return repaired(tools)

    agent, _ = build(documents, tmp_path, script, rag_mode="tool", max_execution_seconds=0.03)
    report = run(agent, {**TASK, "execution_budget": {"wall_clock_seconds": 0.03}})
    assert report["execution"]["termination"]["cause"] == "budget"
    assert report["execution"]["rag"]["searches"] == []


def test_an_empty_corpus_is_refused_outside_a_dry_run(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / ".gitkeep").write_text("", encoding="utf-8")
    with pytest.raises(CorpusError, match="no document to retrieve from"):
        LangChainRAGAgent(LangChainRAGAgentConfig(rag_directory=str(empty)), ani=FakeANI(), model=object())
    agent = LangChainRAGAgent(LangChainRAGAgentConfig(rag_directory=str(empty), dry_run=True),
                              ani=FakeANI(), model=object())
    report = run(agent)
    assert report["execution"]["termination"]["cause"] == "dry_run"
    assert report["execution"]["rag"] == {"searches": [], "tool_searches": 0, "context_excerpts": 0}
    assert report["execution"]["model_parameters"]["rag_corpus"]["chunks"] == 0


@pytest.mark.parametrize("settings, message", [
    ({"rag_mode": "always"}, "unknown rag mode"),
    ({"rag_top_k": 0}, "top-k"),
    ({"rag_top_k": 11}, "top-k"),
])
def test_a_setting_this_subject_cannot_honour_is_refused_at_construction(documents, tmp_path, settings, message):
    with pytest.raises(ValueError, match=message):
        build(documents, tmp_path, **settings)


# --- what is recorded ------------------------------------------------------------

def test_every_parameter_a_run_can_set_is_recorded_with_the_corpus(documents, tmp_path):
    agent, _ = build(documents, tmp_path, rag_top_k=3, rag_chunk_chars=800, rag_chunk_overlap=100)
    parameters = run(agent)["execution"]["model_parameters"]
    missing = [name for name in REQUIRED if name not in parameters]
    assert not missing, f"not recorded: {missing}"
    assert parameters["rag_mode"] == "both"
    assert parameters["rag_retriever"] == "bm25" and parameters["rag_embedding_model"] is None
    assert (parameters["rag_top_k"], parameters["rag_chunk_chars"], parameters["rag_chunk_overlap"]) == (3, 800, 100)
    corpus = parameters["rag_corpus"]
    assert corpus["sha256"] == agent.corpus.sha256
    assert [item["path"] for item in corpus["files"]] == ["qos.md", "srlinux.md"]


def test_the_default_corpus_is_this_subjects_rag_document_directory():
    assert DEFAULT_RAG_DIRECTORY == Path(__file__).resolve().parents[1] / "rag_document"
    assert DEFAULT_RAG_DIRECTORY.is_dir()


def test_the_settings_are_read_under_this_subjects_own_prefix(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_RAG_AGENT_RAG_MODE", "tool")
    monkeypatch.setenv("LANGCHAIN_RAG_AGENT_RAG_TOP_K", "6")
    monkeypatch.setenv("LANGCHAIN_RAG_AGENT_MAX_CONTEXT_CHARS", "9000")
    monkeypatch.setenv("LANGCHAIN_RAG_AGENT_RAG_EMBEDDING_MODEL", "openai/text-embedding-3-small")
    monkeypatch.setenv("LANGCHAIN_AGENT_MAX_CONTEXT_CHARS", "1234")
    config = LangChainRAGAgentConfig.from_env()
    assert (config.rag_mode, config.rag_top_k, config.max_context_chars) == ("tool", 6, 9000)
    assert config.rag_embedding_model == "openai/text-embedding-3-small"
    assert LangChainAgentConfig.from_env().max_context_chars == 1234, "the baseline keeps its own"


# --- the prompt ------------------------------------------------------------------

def test_the_prompt_is_the_baselines_with_the_documentation_added(documents, tmp_path):
    agent, _ = build(documents, tmp_path)
    baseline = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=FakeANI())
    assert "search_documents" in agent.system_prompt
    assert "# Documentation" in agent.system_prompt
    missing = [line for line in baseline.system_prompt.splitlines() if line not in agent.system_prompt]
    assert not missing, f"baseline lines the RAG prompt dropped: {missing}"


def test_a_prompt_variant_is_resolved_in_this_subjects_prompts(documents, tmp_path):
    with pytest.raises(ValueError, match="unknown prompt variant"):
        build(documents, tmp_path, prompt_variant="guided")


# --- a separate corpus for the task-time excerpts ---------------------------------

GUIDE = """# Troubleshooting guide

## Interface administratively disabled

When an interface was administratively disabled, check the host first, then the
gateway port, then the routes; enable the interface and validate.
"""


@pytest.fixture
def guide(tmp_path: Path) -> Path:
    directory = tmp_path / "rag_context"
    directory.mkdir()
    (directory / "guide.md").write_text(GUIDE, encoding="utf-8")
    return directory


def test_a_context_corpus_serves_the_excerpts_and_the_tool_keeps_the_main_corpus(documents, guide, tmp_path):
    seen = {}

    def script(tools):
        seen["search"] = json.loads(tools["search_documents"].invoke({"query": "shaping VyOS qos policy"}))
        return repaired(tools)

    agent, captured = build(documents, tmp_path, script, rag_context_directory=str(guide))
    report = run(agent)
    message = captured["graph"].messages[0]["content"]
    assert "[1] guide.md > Troubleshooting guide" in message
    assert "srlinux.md" not in message and "qos.md" not in message
    assert seen["search"]["ok"] is True
    assert {item["source"] for item in seen["search"]["results"]} <= {"srlinux.md", "qos.md"}
    parameters = report["execution"]["model_parameters"]
    assert parameters["rag_context_corpus"]["files"][0]["path"] == "guide.md"
    assert parameters["rag_corpus"]["chunks"] >= 2
    searches = report["execution"]["rag"]["searches"]
    assert [item["origin"] for item in searches] == ["context", "tool"]
    assert {hit["source"] for hit in searches[0]["results"]} == {"guide.md"}


def test_without_a_context_corpus_one_corpus_serves_both(documents, tmp_path):
    agent, _ = build(documents, tmp_path)
    report = run(agent)
    parameters = report["execution"]["model_parameters"]
    assert parameters["rag_context_directory"] is None
    assert parameters["rag_context_corpus"] is None


def test_an_empty_context_corpus_is_refused(documents, tmp_path):
    empty = tmp_path / "empty_context"
    empty.mkdir()
    with pytest.raises(CorpusError):
        build(documents, tmp_path, rag_context_directory=str(empty))
