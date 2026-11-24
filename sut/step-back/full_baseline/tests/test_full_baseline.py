"""full_baseline: langchain_agent_v2 without get_object. A fake ANI, a scripted model, the real graph."""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from sut.langchain_agent_v2.ani_tools import TOOLS as V2_TOOLS
from sut.langchain_agent_v2.ani_tools import PlainANIAdapter

# Its path has a hyphen (sut/step-back), which no import statement can name.
subject = importlib.import_module("sut.step-back.full_baseline")
agent_module = importlib.import_module("sut.step-back.full_baseline.agent")
FullBaselineAgent, FullBaselineConfig = subject.FullBaselineAgent, subject.FullBaselineConfig
FullBaselineANIAdapter = agent_module.FullBaselineANIAdapter

ROOT = Path(__file__).resolve().parents[4]
PROMPT = ROOT / "sut" / "step-back" / "full_baseline" / "prompts" / "default.txt"
V2_PROMPT = ROOT / "sut" / "langchain_agent_v2" / "prompts" / "default.txt"


class FakeANI:
    def __init__(self):
        self.operations: list[str] = []

    def dispatch(self, operation, arguments, *, task=None):
        self.operations.append(operation)
        if operation == "submit_result":
            return {"ok": True, "operation": operation, "submitted": True, **arguments}
        return {"ok": True, "operation": operation, "nodes": [{"name": "fw", "text": "line"}]}


#: The judge declares every ANI read, get_object included.
TASK = {"intent": "repair", "execution_budget": {"wall_clock_seconds": 60}, "success_criteria": {"all_of": []},
        "tools": ["get_topology", "get_state", "get_running_config", "get_object", "submit_result"]}


def model_replying(*messages):
    class Model(GenericFakeChatModel):
        calls: int = 0
        seen: list = Field(default_factory=list)

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

        def _generate(self, messages, *args, **kwargs):
            self.calls += 1
            self.seen.append(messages)
            return super()._generate(messages, *args, **kwargs)

    return Model(messages=iter(messages))


def call(tool, position, /, **args):
    # Positional only: get_object takes an argument called `name`.
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": f"r{position}", "type": "tool_call"}])


SUBMIT = call("submit_result", "end", status="failed", summary="x")


def run(tmp_path, *messages):
    ani, model = FakeANI(), model_replying(*messages)
    agent = FullBaselineAgent(FullBaselineConfig(artifact_directory=str(tmp_path)), ani=ani, model=model)
    return json.loads(agent.invoke(json.dumps(TASK))), ani, model


def offered(adapter):
    return {item.name: convert_to_openai_tool(item) for item in adapter.tools()}


def test_the_surface_is_v2s_without_get_object(tmp_path):
    adapter = FullBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path)
    assert [item.name for item in adapter.tools()] == [name for name in V2_TOOLS if name != "get_object"]
    assert agent_module.TOOLS == ("get_topology_simple", "get_state", "get_running_config", "update_config",
                                  "update_object", "execute_validation", "rollback_config", "submit_result")


def test_every_other_tool_is_offered_as_v2_offers_it(tmp_path):
    mine = offered(FullBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path))
    v2 = offered(PlainANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path))
    assert mine == {name: schema for name, schema in v2.items() if name != "get_object"}


def test_nothing_the_model_reads_names_get_object(tmp_path):
    for name, schema in offered(FullBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path)).items():
        assert not re.search(r"\bget_object\b", json.dumps(schema)), name
    assert "get_object" not in PROMPT.read_text(encoding="utf-8")


def test_the_prompt_is_v2s_word_for_word_as_in_the_other_step_back_subjects(tmp_path):
    # Its compliance rules included, since 2026-11-21 (Hugo), as in every repair prompt.
    assert PROMPT.read_text(encoding="utf-8") == V2_PROMPT.read_text(encoding="utf-8")
    assert "# Configuration compliance\n" in PROMPT.read_text(encoding="utf-8")
    for other in ("dumb_baseline", "object_baseline"):
        assert PROMPT.read_text(encoding="utf-8") == (PROMPT.parents[2] / other / "prompts" / "default.txt").read_text(
            encoding="utf-8"), other
    agent = FullBaselineAgent(FullBaselineConfig(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object())
    assert agent.system_prompt == PROMPT.read_text(encoding="utf-8").strip()


def test_the_task_lists_the_surface_and_not_get_object(tmp_path):
    _report, _ani, model = run(tmp_path, SUBMIT)
    task = json.loads(next(message.content for message in model.seen[0] if message.type == "human"))
    assert "get_object" not in task["tools"] and "get_running_config" in task["tools"]


def test_a_get_object_call_is_refused_before_the_ani_and_the_episode_goes_on(tmp_path):
    report, ani, model = run(tmp_path, call("get_object", 0, node="fw", kind="interface", name="eth1"),
                             call("get_state", 1, nodes=["fw"]), SUBMIT)
    assert ani.operations == ["get_state", "submit_result"] and model.calls == 3
    refused = [row for row in report["ani_operations"] if row["operation"] == "get_object"]
    assert len(refused) == 1 and refused[0]["dispatched"] is False and "not a valid tool" in refused[0]["error"]
    assert report["execution"]["reads"] == 1
    assert report["sut_identity"] == "full_baseline"


def test_it_reads_its_own_settings(monkeypatch):
    monkeypatch.setenv("FULL_BASELINE_MAX_CONTEXT_CHARS", "2048")
    monkeypatch.setenv("LANGCHAIN_AGENT_V2_MAX_CONTEXT_CHARS", "4096")
    assert FullBaselineConfig.from_env().max_context_chars == 2048
    assert (FullBaselineConfig().keep_recent_reads, FullBaselineConfig().keep_recent_read_chars) == (10, None)
