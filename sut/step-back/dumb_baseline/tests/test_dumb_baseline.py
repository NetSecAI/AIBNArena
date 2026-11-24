"""dumb_baseline: a fake ANI, a scripted model, the real graph."""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from sut.langchain_agent.ani_tools import ExecutionBudgetExceeded
from sut.langchain_agent_v2.ani_tools import TOOLS

# Its path has a hyphen (sut/step-back), which no import statement can name.
subject = importlib.import_module("sut.step-back.dumb_baseline")
agent_module = importlib.import_module("sut.step-back.dumb_baseline.agent")
DumbBaselineAgent, DumbBaselineConfig = subject.DumbBaselineAgent, subject.DumbBaselineConfig
CONFIGURATIONS_HEADER = agent_module.CONFIGURATIONS_HEADER
CONFIGURATIONS_SOURCE = agent_module.CONFIGURATIONS_SOURCE
DumbBaselineANIAdapter = agent_module.DumbBaselineANIAdapter

ROOT = Path(__file__).resolve().parents[4]
PROMPT = ROOT / "sut" / "step-back" / "dumb_baseline" / "prompts" / "default.txt"
V2_PROMPT = ROOT / "sut" / "langchain_agent_v2" / "prompts" / "default.txt"

#: A firewall configuration longer than a tool result may be (`max_context_chars`, 12 000),
#: whose last line shows it arrived whole.
FIREWALL = "\n".join(f"set firewall rule {number} action accept" for number in range(600)) + "\nlast line of fw"
CONFIGURATIONS = {"fw": FIREWALL, "sw1": "interface Ethernet1\n   switchport access vlan 10",
                  "h1": "2: eth1: <UP>\n    inet 192.0.2.10/24\ndefault via 192.0.2.1 dev eth1"}
KINDS = {"fw": "vyos", "sw1": "arista_ceos", "h1": "linux"}
COMMANDS = {"vyos": "show", "arista_ceos": "show running-config", "linux": "ip address show && ip route show"}
TOPOLOGY = {"ok": True, "operation": "get_topology", "segments": [],
            "nodes": [{"name": name, "kind": kind, "roles": [], "capabilities": [], "interfaces": []}
                      for name, kind in KINDS.items()]}


def configuration(name, *, failed=False):
    result = {"target": name, "command": COMMANDS[KINDS[name]], "returncode": 1 if failed else 0, "ok": not failed,
              "safe": True, "stdout": "" if failed else CONFIGURATIONS[name] + "\n",
              "stderr": "Connection refused\n" if failed else "", "reason": None}
    return {"name": name, "kind": KINDS[name], "config": [{"path": "/", "result": result}]}


class FakeANI:
    def __init__(self, *, refuse: str | None = None, failing_node: str | None = None):
        self.refuse, self.failing_node = refuse, failing_node
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, operation, arguments, *, task=None):
        self.calls.append((operation, arguments))
        if operation == self.refuse:
            return {"ok": False, "operation": operation, "error": "refused"}
        if operation == "get_topology":
            return TOPOLOGY
        if operation == "get_running_config":
            nodes = [configuration(name, failed=name == self.failing_node) for name in arguments["nodes"]]
            return {"ok": self.failing_node is None, "operation": operation, "nodes": nodes}
        if operation == "submit_result":
            return {"ok": True, "operation": operation, "submitted": True, **arguments}
        if operation == "execute_validation":
            return {"ok": True, "operation": operation, "checks": [{"type": "public_success_criteria", "passed": True}]}
        return {"ok": True, "operation": operation, "nodes": [{"name": "fw", "text": "state line"}]}


TASK = {"intent": "repair", "execution_budget": {"wall_clock_seconds": 60}, "success_criteria": {"all_of": []},
        "tools": ["get_topology", "get_state", "get_running_config", "submit_result"]}


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


def call(name, position, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"r{position}", "type": "tool_call"}])


SUBMIT = call("submit_result", "end", status="failed", summary="x")


def run(tmp_path, *messages, ani=None, **settings):
    ani = ani or FakeANI()
    model = model_replying(*messages)
    agent = DumbBaselineAgent(DumbBaselineConfig(artifact_directory=str(tmp_path), **settings), ani=ani, model=model)
    return json.loads(agent.invoke(json.dumps(TASK))), ani, model


def human_messages(model, index=0):
    return [message.content for message in model.seen[index] if message.type == "human"]


# --- what the model reads ---------------------------------------------------------------

def test_the_prompt_is_v2s_word_for_word_its_compliance_rules_included():
    """Since 2026-11-21 (Hugo) every repair prompt has `# Configuration compliance`: before, the
    step-back prompts left it out, while the subjects they were compared with had it."""
    assert PROMPT.read_text(encoding="utf-8") == V2_PROMPT.read_text(encoding="utf-8")
    assert "# Configuration compliance\n" in PROMPT.read_text(encoding="utf-8")


def test_the_agent_runs_under_that_prompt(tmp_path):
    agent = DumbBaselineAgent(DumbBaselineConfig(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object())
    assert agent.system_prompt == PROMPT.read_text(encoding="utf-8").strip()


def test_the_tools_are_v2s(tmp_path):
    adapter = DumbBaselineANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path)
    assert [item.name for item in adapter.tools()] == list(TOOLS)


def test_every_configuration_reaches_the_first_model_call_whole(tmp_path):
    report, ani, model = run(tmp_path, SUBMIT)
    assert ani.calls[:2] == [("get_topology", {}), ("get_running_config", {"nodes": ["fw", "sw1", "h1"]})]
    task, configurations = human_messages(model)
    assert json.loads(task)["intent"] == "repair"
    assert configurations.startswith(CONFIGURATIONS_HEADER + "\n\n")
    for name, text in CONFIGURATIONS.items():
        assert f"### {name} ({KINDS[name]}): {COMMANDS[KINDS[name]]}\n{text}" in configurations
    assert len(FIREWALL) > DumbBaselineConfig().max_context_chars and "last line of fw" in configurations
    assert report["sut_identity"] == "dumb_baseline" and model.calls == 1


def test_the_reads_are_the_subjects_and_not_the_models(tmp_path):
    report, _ani, _model = run(tmp_path, call("get_state", 0, nodes=["fw"]), SUBMIT)
    rows = report["ani_operations"]
    assert [(row["operation"], row.get("source")) for row in rows] == [
        ("get_topology", CONFIGURATIONS_SOURCE), ("get_running_config", CONFIGURATIONS_SOURCE),
        ("get_state", None), ("submit_result", None)]
    assert rows[1]["category"] == "read" and rows[1]["arguments"] == {"nodes": ["fw", "sw1", "h1"]}
    execution = report["execution"]
    assert execution["reads"] == 1 and execution["tool_call_count"] == 2
    assert execution["model_turns"][0]["operations"] == [3], "the first turn made the third row"
    record = execution["configurations_before_first_turn"]
    assert record["nodes"] == record["read"] == ["fw", "sw1", "h1"] and record["chars"] > len(FIREWALL)


def test_the_configurations_are_never_stubbed(tmp_path):
    reads = [call("get_state", position, nodes=[f"n{position}"]) for position in range(12)]
    report, _ani, model = run(tmp_path, *reads, SUBMIT)
    assert human_messages(model, -1) == human_messages(model, 0)
    answers = [message for message in model.seen[-1] if message.type == "tool"]
    assert ['"elided": true' in message.content for message in answers] == [True] * 2 + [False] * 10
    assert report["execution"]["context"]["elided_results"] == 2


def test_a_node_whose_read_failed_is_shown_with_the_error(tmp_path):
    report, _ani, model = run(tmp_path, SUBMIT, ani=FakeANI(failing_node="sw1"))
    configurations = human_messages(model)[1]
    assert "### sw1 (arista_ceos): show running-config\nThe read failed: Connection refused" in configurations
    assert "last line of fw" in configurations
    assert report["execution"]["configurations_before_first_turn"]["read"] == ["fw", "h1"]
    assert report["execution"]["failed_operations"] == 0, "the subject's read is no failure of the model's"


@pytest.mark.parametrize("refused", ["get_topology", "get_running_config"])
def test_a_refused_read_is_said_and_the_episode_goes_on(tmp_path, refused):
    report, _ani, model = run(tmp_path, call("get_state", 0, nodes=["fw"]), SUBMIT, ani=FakeANI(refuse=refused))
    configurations = human_messages(model)[1]
    assert f"{refused} failed: refused" in configurations
    assert model.calls == 2 and report["final_response"]["status"] == "failed"


def test_no_read_starts_once_the_budget_is_spent(tmp_path):
    ani = FakeANI()
    adapter = DumbBaselineANIAdapter(ani, task=TASK, artifact_directory=tmp_path, deadline=time.monotonic() - 1)
    with pytest.raises(ExecutionBudgetExceeded):
        adapter.all_configurations()
    assert ani.calls == []


def test_two_subjects_from_one_shell_read_their_own_settings(monkeypatch):
    monkeypatch.setenv("DUMB_BASELINE_MAX_CONTEXT_CHARS", "2048")
    monkeypatch.setenv("LANGCHAIN_AGENT_V2_MAX_CONTEXT_CHARS", "4096")
    assert DumbBaselineConfig.from_env().max_context_chars == 2048
    assert (DumbBaselineConfig().keep_recent_reads, DumbBaselineConfig().keep_recent_read_chars) == (10, None)
