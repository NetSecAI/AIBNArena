"""The baseline with nothing coaching the model: a fake ANI, a scripted model, the real graph."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from benchmarks.platforms.containerlab import MANAGEMENT_PLANE_RULE
from benchmarks.platforms.containerlab.object_writes import compile_update
from sut.langchain_agent import ani_tools as baseline
from sut.langchain_agent_v2 import LangChainAgentV2, LangChainAgentV2Config
from sut.langchain_agent_v2.agent import KeepRecentReads
from sut.langchain_agent_v2.ani_tools import EXAMPLES, TOOLS, PlainANIAdapter, simple_topology

ROOT = Path(__file__).resolve().parents[3]
PROMPT = ROOT / "sut" / "langchain_agent_v2" / "prompts" / "default.txt"
BASELINE_PROMPT = ROOT / "sut" / "langchain_agent" / "prompts" / "default.txt"

#: Words of the advice taken away, in the baseline's descriptions and in the ANI's.
ADVICE = ("prefer", "either of arista", "evaluate your changes first", "before changing configuration",
          "instead of a whole view", "completion gate", "idly", "one change's commands per platform",
          "the name is the destination zone", "reads go through", "safe compensation")


def node(name, kind, *ports, roles=("router",)):
    return {"name": name, "kind": kind, "roles": list(roles), "capabilities": ["ospf"],
            "interfaces": [{"name": port, "segment": segment} for port, segment in ports]}


#: The ANI's answer: a cable, a VLAN shared by three ports, and a trunk with one declared end.
TOPOLOGY = {"ok": True, "operation": "get_topology",
            "nodes": [node("leaf1", "nokia_srlinux", ("ethernet-1/1", "fabric"), ("ethernet-1/10", "users_vlan")),
                      node("spine1", "nokia_srlinux", ("ethernet-1/1", "fabric")),
                      node("fw", "vyos", ("eth1.100", "users_vlan")),
                      node("user1", "linux", ("eth1", "users_vlan"), roles=("client",)),
                      node("sw1", "arista_ceos", ("Ethernet1", "trunk_sw1_fw"))],
            "segments": [{"name": "fabric", "type": "p2p", "members": ["leaf1", "spine1"]}]}


class FakeANI:
    def __init__(self, *, topology=TOPOLOGY, text="line", refuse: str | None = None):
        self.topology, self.text, self.refuse = topology, text, refuse
        self.operations: list[str] = []

    def dispatch(self, operation, arguments, *, task=None):
        self.operations.append(operation)
        if operation == self.refuse:
            return {"ok": False, "operation": operation, "error": "refused"}
        if operation == "get_topology":
            return self.topology
        if operation == "submit_result":
            return {"ok": True, "operation": operation, "submitted": True, **arguments}
        if operation == "execute_validation":
            return {"ok": True, "operation": operation, "checks": [{"type": "public_success_criteria", "passed": True}]}
        return {"ok": True, "operation": operation, "nodes": [{"name": "leaf1", "text": self.text}]}


TASK = {"intent": "repair", "execution_budget": {"wall_clock_seconds": 60}, "success_criteria": {"all_of": []},
        "tools": ["get_topology", "get_state", "submit_result"]}


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


def turn(*calls):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"c{position}", "type": "tool_call"}
        for position, (name, args) in enumerate(calls)])


def run(tmp_path, *messages, ani=None, **settings):
    ani = ani or FakeANI()
    model = model_replying(*messages)
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path), **settings), ani=ani, model=model)
    return json.loads(agent.invoke(json.dumps(TASK))), ani, model


def adapter_for(tmp_path, ani=None, **kwargs):
    return PlainANIAdapter(ani or FakeANI(), task=TASK, artifact_directory=tmp_path, **kwargs)


def tools_of(adapter):
    return {item.name: item for item in adapter.tools()}


def section(prompt: str, heading: str) -> str:
    start = prompt.index(f"# {heading}\n")
    end = prompt.find("\n# ", start + 1)
    return prompt[start:end if end >= 0 else None].rstrip()


# --- what the model reads ---------------------------------------------------------------

def test_the_surface_is_the_baseline_with_the_simple_topology(tmp_path):
    assert [item.name for item in adapter_for(tmp_path).tools()] == list(TOOLS)
    assert list(TOOLS) == ["get_topology_simple", "get_state", "get_running_config", "get_object", "update_config",
                           "update_object", "execute_validation", "rollback_config", "submit_result"]


def test_no_tool_advises_the_model(tmp_path):
    for item in adapter_for(tmp_path).tools():
        offered = json.dumps(convert_to_openai_tool(item)).casefold()
        for words in ADVICE:
            assert words not in offered, f"{item.name} says {words!r}"


def test_no_tool_names_a_tool_this_subject_does_not_have(tmp_path):
    for item in adapter_for(tmp_path).tools():
        offered = json.dumps(convert_to_openai_tool(item))
        assert not re.search(r"\bget_topology\b", offered), f"{item.name} names get_topology"


def test_update_config_keeps_the_management_plane_rule(tmp_path):
    assert MANAGEMENT_PLANE_RULE in tools_of(adapter_for(tmp_path))["update_config"].description


def test_every_tool_shows_example_arguments_its_schema_accepts(tmp_path):
    tools = tools_of(adapter_for(tmp_path))
    assert set(EXAMPLES) == set(TOOLS)
    for name, calls in EXAMPLES.items():
        assert calls, name
        for _, call in calls:
            tools[name].args_schema.model_validate(call)
            assert json.dumps(call) in tools[name].description, name


def test_the_object_write_examples_compile_for_their_platform():
    platforms = {label: platform for platform, (label, _, _) in baseline.UPDATE_CONFIG_EXAMPLES.items()}
    for label, call in EXAMPLES["update_object"]:
        compile_update(platforms[label], call["kind"], call["name"], call["set"])


def test_update_config_shows_every_platform_of_the_baseline_and_none_of_its_rules(tmp_path):
    offered = tools_of(adapter_for(tmp_path))["update_config"].description
    assert ([label for label, _ in EXAMPLES["update_config"]]
            == [label for label, _, _ in baseline.UPDATE_CONFIG_EXAMPLES.values()])
    for _, _, rule in baseline.UPDATE_CONFIG_EXAMPLES.values():
        assert rule not in offered


def test_no_example_carries_a_node_port_or_address_of_a_lab():
    labs = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "scenarios" / "topologies").glob("*.yaml"))
    values = set(re.findall(r"\d+\.\d+\.\d+\.\d+|ethernet-\d+/\d+|Ethernet\d+|\beth\d+\b|\bnode\d+\b", json.dumps(EXAMPLES)))
    assert values
    assert not {value for value in values if value in labs}


def test_the_prompt_states_the_task_and_the_end_and_no_method():
    prompt = PROMPT.read_text(encoding="utf-8")
    for heading in ("# Tools", "# Method", "# Examples"):
        assert heading not in prompt
    assert "get_topology_simple" in prompt and "`get_topology`" not in prompt
    assert "execute_validation" not in prompt and "public_success_criteria" not in prompt


def test_the_prompt_keeps_the_baselines_compliance_rules_word_for_word():
    heading = "Configuration compliance"
    assert (section(PROMPT.read_text(encoding="utf-8"), heading)
            == section(BASELINE_PROMPT.read_text(encoding="utf-8"), heading))


def test_the_agent_runs_under_that_prompt(tmp_path):
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object())
    assert agent.system_prompt == PROMPT.read_text(encoding="utf-8").strip()


# --- nothing corrects the model ---------------------------------------------------------

def test_completed_with_no_validation_reaches_the_ani_at_once(tmp_path):
    report, ani, model = run(tmp_path, turn(("submit_result", {"status": "completed", "summary": "done"})))
    assert ani.operations == ["submit_result"] and model.calls == 1
    assert report["status"] == "completed" and report["verified"] is False
    assert report["execution"]["unverified_completion_pushback"] is None


def test_an_early_failed_reaches_the_ani_at_once(tmp_path):
    report, ani, model = run(tmp_path, turn(("submit_result", {"status": "failed", "summary": "no safe repair"})))
    assert ani.operations == ["submit_result"] and model.calls == 1
    assert report["status"] == "failed"
    assert report["execution"]["early_failure_pushback"] is None


def test_a_reply_with_no_tool_call_ends_the_episode_without_a_nudge(tmp_path):
    report, ani, _model = run(tmp_path, AIMessage(content="Let me think about it."))
    assert report["execution"]["llm_calls"] == 1 and ani.operations == []
    assert report["status"] == "failed"
    assert report["execution"]["termination"]["cause"] == "no_conclusion"
    assert report["execution"]["no_action_nudges"] == []


def test_a_request_failing_again_and_again_carries_no_note(tmp_path):
    adapter = adapter_for(tmp_path, ani=FakeANI(refuse="get_object"))
    read = tools_of(adapter)["get_object"]
    answers = [json.loads(read.invoke({"node": "leaf1", "kind": "interface", "name": "e1"})) for _ in range(5)]
    assert all(baseline.REPEATED_REQUEST_KEY not in answer for answer in answers)
    assert adapter.trace.failed_operations == 5
    assert adapter.trace.repeated_requests.record() == {"limit": None, "action": "none", "notes": [], "resets": []}


def test_a_filter_matches_what_the_device_wrote_and_nothing_else(tmp_path):
    adapter = adapter_for(tmp_path, ani=FakeANI(text="Et3   connected\nEt4   notconnect\n"))
    read = tools_of(adapter)["get_state"]
    missed = json.loads(read.invoke({"nodes": ["sw1"], "line_filter": "Ethernet3"}))
    assert missed["context"]["lines"]["texts"][0]["matching"] == 0
    found = json.loads(read.invoke({"nodes": ["sw1"], "line_filter": "et3"}))
    assert found["context"]["lines"]["texts"][0]["matching"] == 1


def test_a_cut_read_says_it_was_cut_and_not_how_to_read_on(tmp_path):
    adapter = adapter_for(tmp_path, ani=FakeANI(text="a line of a long text\n" * 2000), max_context_chars=1024)
    read = tools_of(adapter)["get_state"]
    answer = json.loads(read.invoke({"nodes": ["leaf1"]}))
    assert answer["context"]["truncated"] is True
    assert "read_further" not in answer["context"]
    paged = json.loads(read.invoke({"nodes": ["leaf1"], "line_offset": 1990, "line_limit": 5}))
    assert paged["context"]["lines"]["texts"][0]["returned"] == 5


# --- get_topology_simple ----------------------------------------------------------------

def test_simple_topology_keeps_nodes_kinds_and_links():
    assert simple_topology(TOPOLOGY) == {
        "ok": True,
        "operation": "get_topology_simple",
        "nodes": [{"name": "leaf1", "kind": "nokia_srlinux"}, {"name": "spine1", "kind": "nokia_srlinux"},
                  {"name": "fw", "kind": "vyos"}, {"name": "user1", "kind": "linux"},
                  {"name": "sw1", "kind": "arista_ceos"}],
        "links": [
            {"endpoint1": {"node": "leaf1", "interface": "ethernet-1/1"},
             "endpoint2": {"node": "spine1", "interface": "ethernet-1/1"}},
            {"endpoints": [{"node": "leaf1", "interface": "ethernet-1/10"},
                           {"node": "fw", "interface": "eth1.100"},
                           {"node": "user1", "interface": "eth1"}]},
            {"endpoints": [{"node": "sw1", "interface": "Ethernet1"}]},
        ],
    }


def test_the_simple_topology_is_one_ani_read_filed_as_one(tmp_path):
    adapter = adapter_for(tmp_path)
    answer = json.loads(tools_of(adapter)["get_topology_simple"].invoke({}))
    data = json.loads(answer["data"])
    assert data["nodes"][0] == {"name": "leaf1", "kind": "nokia_srlinux"}
    assert "roles" not in answer["data"] and "users_vlan" not in answer["data"]
    assert adapter.ani.operations == ["get_topology"]
    assert adapter.trace.operations[0]["operation"] == "get_topology"
    assert adapter.trace.operations[0]["category"] == "read"


def test_a_lab_past_a_page_of_nodes_reaches_the_model_whole(tmp_path):
    # The tool takes no paging argument: a page of 20 nodes, or a cut, would hide the rest.
    nodes = [node(f"node{position}", "linux", ("eth1", f"segment{position}")) for position in range(60)]
    adapter = adapter_for(tmp_path, ani=FakeANI(topology={"ok": True, "operation": "get_topology", "nodes": nodes}),
                          max_context_chars=512)
    answer = json.loads(tools_of(adapter)["get_topology_simple"].invoke({}))
    assert answer["context"]["truncated"] is False
    assert len(json.loads(answer["data"])["nodes"]) == 60


def test_a_failed_topology_read_is_answered_as_the_ani_answered(tmp_path):
    adapter = adapter_for(tmp_path, ani=FakeANI(refuse="get_topology"))
    answer = json.loads(tools_of(adapter)["get_topology_simple"].invoke({}))
    assert json.loads(answer["data"])["error"] == "refused"
    assert adapter.trace.failed_operations == 1


# --- one episode through the real graph -------------------------------------------------

def test_repair_then_submit(tmp_path):
    report, ani, model = run(
        tmp_path,
        turn(("get_topology_simple", {})),
        turn(("update_config", {"changes": [{"target": "leaf1", "commands": ["x"]}]})),
        turn(("execute_validation", {"checks": [{"type": "public_success_criteria"}]})),
        turn(("submit_result", {"status": "completed", "summary": "validated", "diagnosis": "leaf1 x was down"})),
    )
    assert report["sut_identity"] == "langchain_agent_v2"
    assert report["mode"] == "self_execute"
    assert report["status"] == "completed" and report["verified"] is True
    assert ani.operations == ["get_topology", "update_config", "execute_validation", "submit_result"]
    assert model.calls == 4, "the submission ended the graph"
    assert report["execution"]["termination"]["via"] == "submit_result"
    assert report["execution"]["reads"] == 1 and report["execution"]["validations"] == 1
    assert report["final_response"]["diagnosis"] == "leaf1 x was down"


VALIDATE_CALL = ("execute_validation", {"checks": [{"type": "public_success_criteria"}]})
WRITE_CALL = ("update_config", {"changes": [{"target": "leaf1", "commands": ["x"]}]})
COMPLETED_CALL = ("submit_result", {"status": "completed", "summary": "validated"})


def test_a_write_after_the_last_validation_leaves_the_completion_unverified(tmp_path):
    """The validation describes the network before the write, as in the shared loop
    (`self_execute.can_have_changed_the_network`). Until 2026-11-15 it still verified here."""
    for ani in (FakeANI(), FakeANI(refuse="update_config")):
        report, _ani, _model = run(tmp_path, turn(VALIDATE_CALL), turn(WRITE_CALL), turn(COMPLETED_CALL), ani=ani)
        assert ani.operations == ["execute_validation", "update_config", "submit_result"]
        assert report["status"] == "completed" and report["verified"] is False, ani.refuse
        assert report["final_observation"] is None, ani.refuse


def test_a_write_refused_before_any_tool_ran_keeps_the_validation(tmp_path):
    """No write reached the ANI: the network is the one the validation saw, and the graph
    subjects' loop keeps a validation over such a call too (`dispatched: false`)."""
    bad = ("update_config", {**WRITE_CALL[1], "dry_run": True})
    report, ani, _model = run(tmp_path, turn(VALIDATE_CALL), turn(bad), turn(COMPLETED_CALL))
    assert ani.operations == ["execute_validation", "submit_result"]
    assert [row["dispatched"] for row in report["ani_operations"] if row["operation"] == "update_config"] == [False]
    assert report["status"] == "completed" and report["verified"] is True
    assert report["final_observation"]["passed"] is True


def test_the_task_names_the_simple_topology_and_the_episode_uses_only_the_surface(tmp_path):
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object())
    message = json.loads(agent._task_message(TASK, 0.0, list(TOOLS)))
    assert "get_topology_simple" in message["tools"] and "get_topology" not in message["tools"]


# --- the record of an episode the clock ended --------------------------------------------

def test_the_call_the_budget_cut_is_a_turn_of_the_record(tmp_path):
    """As the shared loop files it: a call made, its seconds, and why no answer
    came. Until 2026-11-15 the call `wait_for` cancelled was in no turn and no count."""
    import asyncio

    class SlowModel(GenericFakeChatModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

        async def _agenerate(self, messages, *args, **kwargs):
            await asyncio.sleep(30)
            return await super()._agenerate(messages, *args, **kwargs)

    # The answer only the diagnosis turn reads, through the synchronous call.
    model = SlowModel(messages=iter([AIMessage(content='{"diagnosis": "not found"}')]))
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path)), ani=FakeANI(), model=model)
    report = json.loads(agent.invoke(json.dumps({**TASK, "execution_budget": {"wall_clock_seconds": 1}})))

    execution = report["execution"]
    assert execution["termination"]["cause"] == "budget"
    [cut] = execution["model_turns"]
    assert cut["kind"] == "error" and cut["error"] == "execution budget exceeded" and cut["operations"] == []
    assert 0.5 <= cut["seconds"] <= 5
    assert execution["llm_calls"] == 1 and execution["termination"]["turn"] == 1
    assert execution["model_seconds"] == cut["seconds"]


# --- the diagnosis of an episode with no conclusion --------------------------------------

def test_a_reply_with_no_conclusion_is_asked_for_its_diagnosis(tmp_path):
    report, _ani, model = run(tmp_path, AIMessage(content="not sure"), AIMessage(content='{"diagnosis": "the route is gone"}'))
    assert report["status"] == "failed" and report["execution"]["termination"]["cause"] == "no_conclusion"
    assert report["execution"]["llm_calls"] == 1, "the diagnosis turn is not a turn of the repair"
    diagnosis = report["diagnosis"]
    assert diagnosis["text"] == "the route is gone"
    assert diagnosis["source"] == "termination_turn" and diagnosis["requested"] is True
    question = model.seen[-1]
    assert question[0].type == "system" and question[-2].content == "not sure"
    assert "The episode is over" in question[-1].content


def test_a_tool_call_in_the_diagnosis_turn_is_refused_once(tmp_path):
    report, ani, model = run(tmp_path, AIMessage(content="not sure"), turn(("get_state", {"nodes": ["a"]})),
                             AIMessage(content='{"diagnosis": "d"}'))
    assert report["diagnosis"]["text"] == "d" and report["diagnosis"]["calls"] == 2
    assert ani.operations == []


def test_a_conclusion_needs_no_diagnosis_turn(tmp_path):
    report, _ani, model = run(tmp_path, turn(("submit_result", {"status": "failed", "summary": "x", "diagnosis": "d"})))
    assert model.calls == 1 and "diagnosis" not in report


def test_a_context_overflow_is_not_asked_for_a_diagnosis(tmp_path):
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path)), ani=FakeANI(),
                             model=model_replying(AIMessage(content="never")))
    adapter = adapter_for(tmp_path)
    adapter.transcript = [HumanMessage(content="task")]
    report = agent._report(adapter, None, 60.0, 1.0, "too long", None,
                           termination={"cause": "context_window", "detail": "too long", "turn": 3})
    assert report["diagnosis"]["requested"] is False and "context_window" in report["diagnosis"]["error"]
    assert agent.model.calls == 0


# --- older reads ------------------------------------------------------------------------

SUBMIT = AIMessage(content="", tool_calls=[{"name": "submit_result", "args": {"status": "failed", "summary": "x"},
                                            "id": "end", "type": "tool_call"}])


def call(name, position, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"r{position}", "type": "tool_call"}])


def stubbed(message) -> bool:
    return '"elided": true' in message.content


def read_size(tmp_path, *turns) -> int:
    """How many characters one get_state answer takes in the transcript, the window off."""
    _report, _ani, model = run(tmp_path, *turns, SUBMIT, keep_recent_reads=None, keep_recent_read_chars=None)
    return max(len(message.content) for message in model.seen[-1]
               if message.type == "tool" and message.tool_call_id[1:].isdigit())


def test_the_reads_past_the_character_budget_reach_the_model_as_stubs(tmp_path):
    reads = [call("get_state", position, nodes=[f"n{position}"]) for position in range(6)]
    budget = 3 * read_size(tmp_path, *reads) + read_size(tmp_path, *reads) // 2
    # The character rule alone, as main04 ran it.
    report, _ani, model = run(tmp_path, *reads, SUBMIT, keep_recent_reads=None, keep_recent_read_chars=budget)
    answers = [message for message in model.seen[-1] if message.type == "tool"]
    assert [stubbed(message) for message in answers] == [True] * 3 + [False] * 3
    stub = json.loads(answers[0].content)
    assert stub["operation"] == "get_state" and "n0" in stub["arguments"]
    assert f"up to {budget} characters together" in stub["note"]
    context = report["execution"]["context"]
    assert (context["keep_recent_reads"], context["keep_recent_read_chars"]) == (None, budget)
    assert context["elided_results"] == 3 == sum(event["count"] for event in context["events"])
    assert report["execution"]["model_parameters"]["keep_recent_read_chars"] == budget


def test_the_count_rule_still_holds_when_a_run_sets_it(tmp_path):
    reads = [call("get_state", position, nodes=[f"n{position}"]) for position in range(12)]
    report, _ani, model = run(tmp_path, *reads, SUBMIT, keep_recent_reads=10, keep_recent_read_chars=None)
    answers = [message for message in model.seen[-1] if message.type == "tool"]
    assert [stubbed(message) for message in answers] == [True] * 2 + [False] * 10
    assert json.loads(answers[0].content)["operation"] == "get_state" and "n0" in json.loads(answers[0].content)["arguments"]
    context = report["execution"]["context"]
    assert context["keep_recent_reads"] == 10 and context["elided_results"] == 2
    assert [(event["turn"], event["count"]) for event in context["events"]] == [(12, 1), (13, 1)]
    assert report["execution"]["model_parameters"]["keep_recent_reads"] == 10


def test_the_topology_and_what_is_no_read_stay_whole(tmp_path):
    turns = [call("get_topology_simple", "t"), call("execute_validation", "v", checks=[{"type": "public_success_criteria"}]),
             *(call("get_state", position, nodes=["n"]) for position in range(12))]
    size = read_size(tmp_path, *turns)
    for window in ({"keep_recent_reads": 10, "keep_recent_read_chars": None},
                   {"keep_recent_reads": None, "keep_recent_read_chars": 10 * size + size // 2}):
        _report, _ani, model = run(tmp_path, *turns, SUBMIT, **window)
        answers = {message.tool_call_id: message for message in model.seen[-1] if message.type == "tool"}
        assert not stubbed(answers["rt"]) and not stubbed(answers["rv"]), window
        assert stubbed(answers["r0"]) and stubbed(answers["r1"]) and not stubbed(answers["r2"]), window


def test_the_answer_to_the_last_turn_reaches_the_model_whole_whatever_its_size(tmp_path):
    reads = [call("get_state", position, nodes=[f"n{position}"]) for position in range(3)]
    _report, _ani, model = run(tmp_path, *reads, SUBMIT, keep_recent_reads=None, keep_recent_read_chars=1)
    seen = [[stubbed(message) for message in call_messages if message.type == "tool"] for call_messages in model.seen]
    assert seen[1:] == [[False], [True, False], [True, True, False]], "each answer is seen whole once"


def test_the_rule_changes_what_is_sent_and_not_the_transcript():
    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, messages):
            return Request(messages)

    messages = [HumanMessage(content="task")]
    for position in range(3):
        messages += [call("get_state", position), ToolMessage(content=f"answer {position}", tool_call_id=f"r{position}")]
    events: list = []
    middleware = KeepRecentReads(1, events)
    sent = middleware.wrap_model_call(Request(messages), lambda request: request).messages
    assert [stubbed(message) for message in sent if message.type == "tool"] == [True, True, False]
    assert [message.content for message in messages if message.type == "tool"] == ["answer 0", "answer 1", "answer 2"]
    middleware.wrap_model_call(Request(messages), lambda request: request)
    assert [event["count"] for event in events] == [2], "an older read is counted once"


def test_it_keeps_ten_reads_and_can_be_turned_off(monkeypatch, tmp_path):
    from sut.common.self_execute import KEEP_RECENT_READ_CHARS

    default = LangChainAgentV2Config()
    assert (default.keep_recent_reads, default.keep_recent_read_chars) == (10, None)
    for variable in ("LANGCHAIN_AGENT_V2_KEEP_RECENT_READS", "IBN_SUT_KEEP_RECENT_READS"):
        monkeypatch.delenv("LANGCHAIN_AGENT_V2_KEEP_RECENT_READS", raising=False)
        monkeypatch.setenv(variable, "0")
        config = LangChainAgentV2Config.from_env()
        assert (config.keep_recent_reads, config.keep_recent_read_chars) == (None, None), variable
        agent = LangChainAgentV2(config, ani=FakeANI(), model=object())
        assert not any(isinstance(item, KeepRecentReads) for item in agent._middleware(adapter_for(tmp_path)))
    # main04's rule, in characters alone, stays one setting away.
    monkeypatch.setenv("IBN_SUT_KEEP_RECENT_READ_CHARS", str(KEEP_RECENT_READ_CHARS))
    config = LangChainAgentV2Config.from_env()
    assert (config.keep_recent_reads, config.keep_recent_read_chars) == (None, KEEP_RECENT_READ_CHARS)


def test_two_subjects_from_one_shell_read_their_own_settings(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_AGENT_V2_MAX_CONTEXT_CHARS", "2048")
    monkeypatch.setenv("LANGCHAIN_AGENT_MAX_CONTEXT_CHARS", "4096")
    assert LangChainAgentV2Config.from_env().max_context_chars == 2048


def _argument_names(tool: Any) -> set[str]:
    return set(convert_to_openai_tool(tool)["function"]["parameters"].get("properties", {}))


def test_every_call_keeps_the_baselines_arguments(tmp_path):
    ours, theirs = tools_of(adapter_for(tmp_path)), {
        item.name: item for item in baseline.LangChainANIAdapter(FakeANI(), task=TASK, artifact_directory=tmp_path).tools()}
    for name in set(TOOLS) - {"get_topology_simple"}:
        assert _argument_names(ours[name]) == _argument_names(theirs[name]), name


# -- a call refused before any tool runs --------------------------------------------------


def _refused_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in report["ani_operations"] if item.get("dispatched") is False]


def test_a_call_whose_arguments_are_refused_is_filed_as_not_dispatched(tmp_path):
    from benchmarks.core.sut_client import _operation_counts

    bad = {"changes": [{"target": "leaf1", "commands": ["x"]}], "dry_run": True}
    report, ani, model = run(tmp_path, turn(("update_config", bad)),
                             turn(("submit_result", {"status": "failed", "summary": "x"})))
    answer = next(message for message in model.seen[-1] if message.type == "tool")
    assert answer.status == "error" and ani.operations == ["submit_result"]
    [row] = _refused_rows(report)
    assert (row["operation"], row["ok"], row["error"], row["arguments"]) == ("update_config", False, answer.content, bad)
    counts = _operation_counts(report)
    assert counts.failed_operations == 1 and counts.ani_mutations == 0
    assert report["execution"]["failed_operations"] == 1 and report["execution"]["mutations"] == 0


def test_a_name_no_tool_has_is_filed_as_not_dispatched(tmp_path):
    from benchmarks.core.sut_client import _operation_counts

    report, ani, model = run(tmp_path, turn(("get_status", {"node": "leaf1"})),
                             turn(("submit_result", {"status": "failed", "summary": "x"})))
    answer = next(message for message in model.seen[-1] if message.type == "tool")
    [row] = _refused_rows(report)
    assert (row["operation"], row["error"]) == ("get_status", answer.content)
    assert answer.content.startswith("Error: get_status is not a valid tool, try one of [")
    assert _operation_counts(report).failed_operations == 1 and _operation_counts(report).ani_reads == 0


def test_a_call_the_tool_answers_is_filed_once_as_before(tmp_path):
    report, ani, _model = run(tmp_path, turn(("get_state", {"nodes": ["leaf1"]})),
                              turn(("submit_result", {"status": "failed", "summary": "x"})))
    assert [item["operation"] for item in report["ani_operations"]] == ["get_state", "submit_result"]
    assert not _refused_rows(report)


def test_the_model_reads_the_task_without_the_rate_the_ani_holds_the_flow_to(tmp_path):
    """The judge hands the subject the rate the protected flow measured on the healthy network,
    for its ANI's throughput check. In main04 the model, which read it under `observation`, took
    it for the rate under the fault in turn 1 of all 8 QoS episodes; it now reads the task
    without it, and the ANI still gets the whole task."""
    service = {"source": "host-access-1", "destination": "srv-site", "destination_ip": "10.1.0.10",
               "healthy_throughput_mbps": 7.651}
    task = {**TASK, "tools": [*TASK["tools"], "execute_validation"], "observation": {"service_measurement": service}}
    handed = []

    class RecordingANI(FakeANI):
        def dispatch(self, operation, arguments, *, task=None):
            handed.append((operation, task))
            return super().dispatch(operation, arguments, task=task)

    model = model_replying(call("execute_validation", "v", checks=[{"type": "public_success_criteria"}]), SUBMIT)
    agent = LangChainAgentV2(LangChainAgentV2Config(artifact_directory=str(tmp_path)), ani=RecordingANI(), model=model)
    agent.invoke(json.dumps(task))

    shown = " ".join(str(message.content) for message in model.seen[0] if message.type == "human")
    assert "10.1.0.10" in shown and "healthy_throughput_mbps" not in shown
    [validation_task] = [task for operation, task in handed if operation == "execute_validation"]
    assert validation_task["observation"]["service_measurement"]["healthy_throughput_mbps"] == 7.651
