"""object_baseline: the topology before the first turn, then get_object as the only read of a device.
A fake ANI, a scripted model, the real graph."""
from __future__ import annotations

import importlib
import json
import re
import time
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from sut.langchain_agent.ani_tools import ExecutionBudgetExceeded
from sut.langchain_agent_v2.ani_tools import TOOLS as V2_TOOLS
from sut.langchain_agent_v2.ani_tools import PlainANIAdapter, simple_topology

# Its path has a hyphen (sut/step-back), which no import statement can name.
subject = importlib.import_module("sut.step-back.object_baseline")
agent_module = importlib.import_module("sut.step-back.object_baseline.agent")
ObjectBaselineAgent, ObjectBaselineConfig = subject.ObjectBaselineAgent, subject.ObjectBaselineConfig
ObjectBaselineANIAdapter = agent_module.ObjectBaselineANIAdapter
TOPOLOGY_HEADER, TOPOLOGY_SOURCE = agent_module.TOPOLOGY_HEADER, agent_module.TOPOLOGY_SOURCE

ROOT = Path(__file__).resolve().parents[4]
STEP_BACK = ROOT / "sut" / "step-back"
PROMPT = STEP_BACK / "object_baseline" / "prompts" / "default.txt"

TOPOLOGY = {"ok": True, "operation": "get_topology", "segments": [],
            "nodes": [{"name": "fw", "kind": "vyos", "roles": ["firewall"], "capabilities": [],
                       "interfaces": [{"name": "eth1", "segment": "lan"}]},
                      {"name": "h1", "kind": "linux", "roles": ["client"], "capabilities": [],
                       "interfaces": [{"name": "eth1", "segment": "lan"}]}]}


class FakeANI:
    def __init__(self, *, refuse: str | None = None):
        self.refuse = refuse
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, operation, arguments, *, task=None):
        self.calls.append((operation, arguments))
        if operation == self.refuse:
            return {"ok": False, "operation": operation, "error": "refused"}
        if operation == "get_topology":
            return TOPOLOGY
        if operation == "submit_result":
            return {"ok": True, "operation": operation, "submitted": True, **arguments}
        return {"ok": True, "operation": operation, "node": "fw", "kind": "zone", "name": "LAN", "found": True,
                "output": "set firewall zone LAN default-action 'drop'"}


#: The judge declares every ANI read.
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
OBJECT = call("get_object", 0, node="fw", kind="zone", name="LAN")


def run(tmp_path, *messages, ani=None):
    ani = ani or FakeANI()
    model = model_replying(*messages)
    agent = ObjectBaselineAgent(ObjectBaselineConfig(artifact_directory=str(tmp_path)), ani=ani, model=model)
    return json.loads(agent.invoke(json.dumps(TASK))), ani, model


def human_messages(model, index=0):
    return [message.content for message in model.seen[index] if message.type == "human"]


def offered(adapter):
    return {item.name: convert_to_openai_tool(item) for item in adapter.tools()}


# --- what the model reads ---------------------------------------------------------------

def test_the_only_read_of_a_device_is_get_object(tmp_path):
    adapter = ObjectBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path)
    assert [item.name for item in adapter.tools()] == [
        name for name in V2_TOOLS if name not in ("get_state", "get_running_config")]
    assert agent_module.TOOLS == ("get_topology_simple", "get_object", "update_config", "update_object",
                                  "execute_validation", "rollback_config", "submit_result")


def test_every_tool_it_keeps_is_offered_as_v2_offers_it(tmp_path):
    mine = offered(ObjectBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path))
    v2 = offered(PlainANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path))
    assert mine == {name: schema for name, schema in v2.items() if name in mine}
    assert "config_section" in json.dumps(mine["get_object"]), "every kind of ANI v0.9"


def test_nothing_the_model_reads_names_a_whole_view(tmp_path):
    for name, schema in offered(ObjectBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path)).items():
        assert not re.search(r"\bget_(state|running_config)\b", json.dumps(schema)), name


def test_the_prompt_is_the_step_back_prompt(tmp_path):
    for other in ("dumb_baseline", "full_baseline"):
        assert PROMPT.read_text(encoding="utf-8") == (STEP_BACK / other / "prompts" / "default.txt").read_text(
            encoding="utf-8"), other
    # langchain_agent_v2's prompt word for word, its compliance rules included (2026-11-21).
    assert PROMPT.read_text(encoding="utf-8") == (ROOT / "sut" / "langchain_agent_v2" / "prompts" / "default.txt"
                                                  ).read_text(encoding="utf-8")
    agent = ObjectBaselineAgent(ObjectBaselineConfig(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object())
    assert agent.system_prompt == PROMPT.read_text(encoding="utf-8").strip()


# --- the topology before the first turn, the objects after ------------------------------

def test_the_model_gets_the_topology_and_reads_every_object_itself(tmp_path):
    report, ani, model = run(tmp_path, OBJECT, SUBMIT)
    assert [operation for operation, _arguments in ani.calls] == ["get_topology", "get_object", "submit_result"]
    task, topology = human_messages(model)
    assert "get_object" in json.loads(task)["tools"]
    assert not {"get_state", "get_running_config"} & set(json.loads(task)["tools"])
    assert topology == f"{TOPOLOGY_HEADER}\n\n" + json.dumps(simple_topology(TOPOLOGY), separators=(",", ":"))
    assert report["sut_identity"] == "object_baseline" and model.calls == 2


def test_the_topology_read_is_the_subjects_and_the_object_reads_are_the_models(tmp_path):
    report, _ani, _model = run(tmp_path, OBJECT, SUBMIT)
    rows = report["ani_operations"]
    assert [(row["operation"], row.get("source")) for row in rows] == [
        ("get_topology", TOPOLOGY_SOURCE), ("get_object", None), ("submit_result", None)]
    execution = report["execution"]
    assert execution["reads"] == 1 and execution["tool_call_count"] == 2
    assert execution["topology_before_first_turn"]["ok"] is True
    assert (execution["topology_before_first_turn"]["nodes"], execution["topology_before_first_turn"]["links"]) == (2, 1)


def test_the_topology_stays_whole_while_older_reads_become_stubs(tmp_path):
    reads = [call("get_object", position, node="fw", kind="zone", name=f"Z{position}") for position in range(12)]
    report, _ani, model = run(tmp_path, *reads, SUBMIT)
    assert human_messages(model, -1) == human_messages(model, 0)
    answers = [message for message in model.seen[-1] if message.type == "tool"]
    assert ['"elided": true' in message.content for message in answers] == [True] * 2 + [False] * 10
    assert report["execution"]["context"]["elided_results"] == 2


@pytest.mark.parametrize("view", ["get_running_config", "get_state"])
def test_a_whole_view_the_model_asks_for_is_refused_before_the_ani(tmp_path, view):
    report, ani, model = run(tmp_path, call(view, 0, nodes=["fw"]), OBJECT, SUBMIT)
    assert [operation for operation, _arguments in ani.calls] == ["get_topology", "get_object", "submit_result"]
    refused = [row for row in report["ani_operations"] if row["operation"] == view]
    assert len(refused) == 1 and refused[0]["dispatched"] is False and "not a valid tool" in refused[0]["error"]
    assert model.calls == 3 and report["execution"]["reads"] == 1


def test_a_refused_topology_read_is_said_and_the_episode_goes_on(tmp_path):
    report, _ani, model = run(tmp_path, OBJECT, SUBMIT, ani=FakeANI(refuse="get_topology"))
    assert human_messages(model)[1] == f"{TOPOLOGY_HEADER}\n\nget_topology_simple failed: refused"
    assert report["execution"]["topology_before_first_turn"]["ok"] is False and model.calls == 2


def test_no_read_starts_once_the_budget_is_spent(tmp_path):
    ani = FakeANI()
    adapter = ObjectBaselineANIAdapter(ani, task=TASK, artifact_directory=tmp_path, deadline=time.monotonic() - 1)
    with pytest.raises(ExecutionBudgetExceeded):
        adapter.topology_message()
    assert ani.calls == []


def test_it_reads_its_own_settings(monkeypatch):
    monkeypatch.setenv("OBJECT_BASELINE_MAX_CONTEXT_CHARS", "2048")
    monkeypatch.setenv("LANGCHAIN_AGENT_V2_MAX_CONTEXT_CHARS", "4096")
    assert ObjectBaselineConfig.from_env().max_context_chars == 2048
