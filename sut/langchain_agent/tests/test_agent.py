from __future__ import annotations

import json
import asyncio

import pytest

from sut.common.thinking import ThinkingUnsupported
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent


class FakeANI:
    def dispatch(self, operation, arguments, *, task=None):
        if operation == "submit_result":
            # As the platform's ANI answers an accepted submission.
            return {"ok": True, "operation": operation, "submitted": True, **arguments}
        if operation == "update_config":
            return {"ok": True, "operation": operation, "transaction_id": "tx1", "changes": [{"target": "leaf1", "commands": ["set / interface x admin-state enable"], "result": {"ok": True, "safe": True}}]}
        if operation == "execute_validation":
            return {"ok": True, "operation": operation, "checks": [{"type": "public_success_criteria", "passed": True}]}
        return {"ok": True, "operation": operation}


class FakeGraph:
    def __init__(self, tools):
        self.tools = {item.name: item for item in tools}
        self.invocation_config = None

    async def ainvoke(self, state, config=None):
        self.invocation_config = config
        self.tools["update_config"].invoke({"changes": [{"target": "leaf1", "commands": ["set / interface x admin-state enable"]}]})
        self.tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
        self.tools["submit_result"].invoke({"status": "completed", "summary": "validated",
                                            "diagnosis": "leaf1 interface x was admin-down"})
        return {"messages": [{"role": "assistant", "usage_metadata": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}, "response_metadata": {"model_name": "provider/langchain-model"}}]}


def test_create_agent_factory_and_submit_result_are_used(tmp_path):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        captured["graph"] = FakeGraph(kwargs["tools"])
        return captured["graph"]

    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=FakeANI(), model=object(), agent_factory=factory)
    report = json.loads(agent.invoke(json.dumps({"intent": "repair", "execution_budget": {"wall_clock_seconds": 5}, "success_criteria": {"all_of": [{"type": "lab_connectivity"}]}})))
    assert report["mode"] == "self_execute"
    assert report["status"] == "completed"
    assert report["verified"] is True
    assert report["execution"]["reads"] == 0
    assert report["execution"]["mutations"] == 1
    assert report["execution"]["successful_mutations"] == 1
    assert report["execution"]["validations"] == 1
    assert report["sut_identity"] == "langchain_agent"
    assert report["model_reported_by_sut"] == agent.config.model
    assert report["provider_reported_model"] == "provider/langchain-model"
    assert report["execution"]["llm_calls"] == 1
    assert report["execution"]["token_usage"] == {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}
    assert report["device_changes"][0]["verified_after_action"] is True
    assert captured["model"] is agent.model
    assert "response_format" not in captured, "submit_result is the conclusion tool; langchain's is not added"
    assert [getattr(item, "name", type(item).__name__) for item in captured["middleware"]] == [
        "stop_after_submission", "nudge_on_no_action", "SubmissionInListedOrder"], (
        "the submission stop, the nudge on a text reply, then the listed order around a submission")
    assert captured["graph"].invocation_config["recursion_limit"] == 10_000, "a runaway guard, not a budget"
    assert report["ani_operations"][0]["arguments"]["changes"][0]["target"] == "leaf1"
    # The loop's own keys for how the episode ended, so the summary reads this
    # subject's end from the name it gives.
    assert report["final_response"] == {"status": "completed", "summary": "validated",
                                        "diagnosis": "leaf1 interface x was admin-down"}
    assert report["execution"]["termination"] == {"cause": "own_conclusion", "detail": "completed", "turn": 1,
                                                  "via": "submit_result"}
    assert report["execution"]["conclusion_source"] == "submit_result"
    assert report["ani_operations"][-1]["category"] == "submission"
    assert report["execution"]["interaction_limits"]["ani"]["limit"] is None
    assert list(report["execution"]["interaction_limits"]) == ["ani"]


def test_wall_clock_budget_cancels_the_graph(tmp_path):
    class SlowGraph:
        async def ainvoke(self, state, config=None):
            await asyncio.sleep(1)
            return {"messages": [{"role": "assistant", "content": '{"status": "completed", "summary": "late"}'}]}

    agent = LangChainRepairAgent(
        LangChainAgentConfig(artifact_directory=str(tmp_path), max_execution_seconds=0.02),
        ani=FakeANI(),
        model=object(),
        agent_factory=lambda **kwargs: SlowGraph(),
    )
    report = json.loads(agent.invoke(json.dumps({"intent": "repair"})))
    assert report["status"] == "failed"
    assert report["error"] == "execution budget exceeded"
    assert report["execution"]["elapsed_seconds"] < 0.5
    assert report["execution"]["termination"]["cause"] == "budget"
    assert report["final_response"] is None, "the clock ended it; the subject concluded nothing"


# --- thinking: the request sent and the record written come from one plan ---------

TASK = {"intent": "repair", "execution_budget": {"wall_clock_seconds": 5},
        "success_criteria": {"all_of": [{"type": "lab_connectivity"}]}}


def _capturing_factory(captured):
    def factory(**kwargs):
        captured.update(kwargs)
        captured["graph"] = FakeGraph(kwargs["tools"])
        return captured["graph"]
    return factory


def test_the_thinking_request_is_what_the_record_says(tmp_path):
    """Before this the record said chat_template while ChatLiteLLM was sent nothing.

    ChatLiteLLM forwards `model_kwargs` to litellm.completion and drops an unknown
    keyword such as extra_body= without a word, so the flag has to travel inside
    model_kwargs -- and the record must describe exactly that request.
    """
    captured = {}
    config = LangChainAgentConfig(model="openai/qwen3-8b-think", api_key="k",
                                  enable_thinking=True, artifact_directory=str(tmp_path))
    agent = LangChainRepairAgent(config, ani=FakeANI(), agent_factory=_capturing_factory(captured))
    report = json.loads(agent.invoke(json.dumps(TASK)))

    expected = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
    model = captured["model"]
    assert model.model_kwargs == expected
    assert model._client_params["extra_body"] == expected["extra_body"], \
        "the flag has to reach the kwargs litellm.completion is called with"
    parameters = report["execution"]["model_parameters"]
    assert parameters["thinking_request"] == expected
    assert parameters["thinking_mechanism"] == "chat_template"
    assert parameters["enable_thinking"] is True


def test_saying_nothing_sends_nothing(tmp_path):
    """The default behaviour is pinned: no condition, no flag, mechanism unset."""
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen3-8b-think", artifact_directory=str(tmp_path)),
        ani=FakeANI())
    assert agent._model().model_kwargs == {}
    captured = {}
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen3-8b-think", api_key="k", artifact_directory=str(tmp_path)),
        ani=FakeANI(), agent_factory=_capturing_factory(captured))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["model_parameters"]["thinking_mechanism"] == "unset"
    assert report["execution"]["model_parameters"]["thinking_request"] == {}


def test_a_forced_tool_choice_is_bound_as_auto(tmp_path):
    """A forced tool choice is, under vLLM, a JSON grammar after the reasoning block,
    and a model that never closes its <think> block (Qwen3.5-9B, most turns after a
    tool result) then returns finish=tool_calls with an empty list and no content,
    ending the graph on turn two. The subject binds "auto" unless told otherwise, and
    says so in the record."""
    captured = {}
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen35-9b-think", api_key="k",
                             enable_thinking=True, artifact_directory=str(tmp_path)),
        ani=FakeANI(), agent_factory=_capturing_factory(captured))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    model = captured["model"]
    for forced in (None, "any", "required"):
        bound = model.bind_tools([{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
                                 tool_choice=forced)
        assert bound.kwargs["tool_choice"] == "auto", forced
    named = model.bind_tools([{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
                             tool_choice="none")
    assert named.kwargs["tool_choice"] == "none", "only the forcing values are replaced"
    assert model.model_kwargs == {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}, \
        "the thinking request still travels with the relaxed binding"
    assert report["execution"]["model_parameters"]["tool_choice"] == "auto"


def test_required_forces_a_tool_call_without_langchains_conclusion_tool(tmp_path):
    # create_agent binds no tool_choice once there is no response_format: "required"
    # is bound by the model itself, and submit_result is then the only end.
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen35-9b-think", api_key="k", tool_choice="required",
                             artifact_directory=str(tmp_path)),
        ani=FakeANI())
    for asked in (None, "any"):
        bound = agent._model().bind_tools(
            [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}], tool_choice=asked)
        assert bound.kwargs["tool_choice"] == "required", asked


def test_thinking_off_is_a_condition_that_is_actually_sent(tmp_path):
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen3-8b-think", enable_thinking=False,
                             artifact_directory=str(tmp_path)),
        ani=FakeANI())
    assert agent._model().model_kwargs == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}


def test_a_supplied_model_cannot_claim_a_thinking_condition(tmp_path):
    """Nothing here can attach the request to a model it did not build."""
    with pytest.raises(ValueError, match="supplied model"):
        LangChainRepairAgent(
            LangChainAgentConfig(model="openai/qwen3-8b-think", enable_thinking=True,
                                 artifact_directory=str(tmp_path)),
            ani=FakeANI(), model=object())


def test_an_unsupported_family_is_refused_at_construction(tmp_path):
    """A family with no switch cannot be asked; refused by name, not sent a flag."""
    with pytest.raises(ThinkingUnsupported):
        LangChainRepairAgent(
            LangChainAgentConfig(model="openai/gemma-3-12b", enable_thinking=True,
                                 artifact_directory=str(tmp_path)),
            ani=FakeANI())


def test_the_prompt_route_puts_the_directive_in_front(tmp_path):
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/gpt-5.4", enable_thinking=True,
                             thinking_style="system_prompt", thinking_directive="Think first.",
                             artifact_directory=str(tmp_path)),
        ani=FakeANI())
    assert agent.system_prompt.startswith("Think first.\n\n")
    untouched = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/gpt-5.4", enable_thinking=False,
                             thinking_style="system_prompt", thinking_directive="Think first.",
                             artifact_directory=str(tmp_path)),
        ani=FakeANI())
    assert not untouched.system_prompt.startswith("Think first.")


# --- how an episode ended, named -----------------------------------------------------

class FailingANI(FakeANI):
    def dispatch(self, operation, arguments, *, task=None):
        if operation == "get_state":
            return {"ok": False, "operation": operation, "error": "node unreachable"}
        return super().dispatch(operation, arguments, task=task)


def _agent(tmp_path, graph_factory, ani=None, **config):
    return LangChainRepairAgent(
        LangChainAgentConfig(artifact_directory=str(tmp_path), **config),
        ani=ani or FakeANI(), model=object(), agent_factory=graph_factory)


def _graph_returning(state):
    class Graph:
        def __init__(self, tools):
            self.tools = {item.name: item for item in tools}

        async def ainvoke(self, _state, config=None):
            if callable(state):
                return state(self.tools)
            return state
    return lambda **kwargs: Graph(kwargs["tools"])


def _submitting(status, summary, diagnosis=None, before=None):
    def script(tools):
        if before:
            before(tools)
        # A `failed` with more than half the budget left is sent back once
        # (`_early_failure_pushback`); the model that means it submits it again.
        for _ in range(2 if status == "failed" else 1):
            tools["submit_result"].invoke({"status": status, "summary": summary, "diagnosis": diagnosis})
        return {"messages": []}
    return script


def test_a_self_declared_failure_is_its_own_conclusion(tmp_path):
    agent = _agent(tmp_path, _graph_returning(_submitting("failed", "no safe repair")))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["status"] == "failed"
    assert report["error"] == "no safe repair", "the loop files a self-declared failure under error"
    assert report["final_response"] == {"status": "failed", "summary": "no safe repair", "diagnosis": None}
    assert report["execution"]["termination"]["cause"] == "own_conclusion"


def test_an_unvalidated_completion_is_sent_back_once_then_recorded_as_declared(tmp_path):
    # The first `completed` no passing validation backs is sent back; the second ends the
    # episode whatever it claims: the claim is kept, and `verified` says that no
    # validation passed. The judge's oracles decide anyway.
    def resubmit(tools):
        tools["submit_result"].invoke({"status": "completed", "summary": "done",
                                       "diagnosis": "leaf1 x admin-down"})
    agent = _agent(tmp_path, _graph_returning(
        _submitting("completed", "done", "leaf1 x admin-down", before=resubmit)))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    submissions = [op for op in report["ani_operations"] if op["category"] == "submission"]
    assert [op["ok"] for op in submissions] == [False, True], "the refusal is in the record"
    assert report["execution"]["unverified_completion_pushback"]["reason"] == "no execute_validation was run"
    assert report["status"] == "completed"
    assert report["verified"] is False
    assert report["error"] is None
    assert report["execution"]["termination"]["cause"] == "own_conclusion"
    assert report["execution"]["termination"]["via"] == "submit_result"
    assert report["final_response"]["diagnosis"] == "leaf1 x admin-down"


def test_a_completion_written_as_text_without_validation_is_refused(tmp_path):
    agent = _agent(tmp_path, _graph_returning(
        {"messages": [{"role": "assistant", "content": '{"status": "completed", "summary": "done"}'}]}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["status"] == "failed"
    assert report["final_response"]["status"] == "completed", "what the subject claimed"
    assert report["execution"]["termination"]["cause"] == "completion_rejected"
    assert "without a passing public validation" in report["execution"]["termination"]["detail"]


def test_a_submission_wins_over_the_text_that_follows(tmp_path):
    def submitted_then_text(tools):
        for _ in range(2):  # the first, this early, is sent back
            tools["submit_result"].invoke({"status": "failed", "summary": "no safe repair"})
        return {"messages": [{"role": "assistant", "content": CONCLUSION_TEXT}]}
    agent = _agent(tmp_path, _graph_returning(submitted_then_text))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["conclusion_source"] == "submit_result"
    assert report["status"] == "failed"


def test_a_refused_submission_concludes_nothing(tmp_path):
    class RefusingANI(FakeANI):
        def dispatch(self, operation, arguments, *, task=None):
            if operation == "submit_result":
                return {"ok": False, "operation": operation, "error": "submit_result.summary must state ..."}
            return super().dispatch(operation, arguments, task=task)
    agent = _agent(tmp_path, _graph_returning(_submitting("completed", "x")), ani=RefusingANI())
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"]["cause"] == "no_conclusion"
    assert report["execution"]["failed_operations"] == 1


def test_three_identical_failed_requests_end_nothing_and_the_record_names_the_note(tmp_path):
    """Until 2026-10-30 the third ended the episode (`repeated_failed_requests`)."""
    def three_failed_reads(tools):
        for _ in range(3):
            tools["get_state"].invoke({"nodes": ["leaf1"]})
        return {"messages": [{"role": "assistant", "content": '{"status": "failed", "summary": "x"}'}]}
    agent = _agent(tmp_path, _graph_returning(three_failed_reads), ani=FailingANI())
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"]["cause"] == "own_conclusion"
    assert report["execution"]["repeated_requests"] == {
        "limit": 3, "action": "note", "notes": [{"operation": "get_state", "counted": [0, 1, 2]}],
        "resets": []}
    assert "stop_rule" not in report["execution"]
    assert report["final_response"]["status"] == "failed"


CONCLUSION_TEXT = ('{"status": "completed", "summary": "leaf2 ethernet-1/50 was admin-down; '
                   'enabled it; the public validation passed"}')


def test_a_conclusion_written_as_json_text_is_the_conclusion(tmp_path):
    # The prompt says "return only JSON"; a model that obeys never calls the
    # structured-output tool, and its answer must not be filed as no conclusion.
    def validated_then_text(tools):
        tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
        return {"messages": [{"role": "assistant", "content": CONCLUSION_TEXT}]}
    agent = _agent(tmp_path, _graph_returning(validated_then_text))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["status"] == "completed"
    assert report["final_response"] == {"status": "completed", "summary": json.loads(CONCLUSION_TEXT)["summary"]}
    assert report["execution"]["termination"]["cause"] == "own_conclusion"
    assert report["execution"]["conclusion_source"] == "final_message"


def test_a_submitted_conclusion_says_where_it_came_from(tmp_path):
    agent = _agent(tmp_path, _graph_returning(_submitting("failed", "no safe repair")))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["conclusion_source"] == "submit_result"


def test_a_fenced_json_conclusion_is_read_too(tmp_path):
    fenced = "```json\n" + CONCLUSION_TEXT + "\n```"
    agent = _agent(tmp_path, _graph_returning({"messages": [{"role": "assistant", "content": fenced}]}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["final_response"]["status"] == "completed"
    assert report["execution"]["termination"]["cause"] == "completion_rejected", "no validation passed"


def test_text_that_is_not_the_contract_stays_no_conclusion(tmp_path):
    for content in ("I enabled the interface and everything works now.",
                    '{"status": "done", "summary": "wrong status value"}',
                    '{"ok": true}'):
        agent = _agent(tmp_path, _graph_returning({"messages": [{"role": "assistant", "content": content}]}))
        report = json.loads(agent.invoke(json.dumps(TASK)))
        assert report["execution"]["termination"]["cause"] == "no_conclusion", content
        assert report["execution"]["conclusion_source"] is None


def test_a_tool_message_that_looks_like_a_conclusion_is_not_one(tmp_path):
    agent = _agent(tmp_path, _graph_returning({"messages": [{"role": "tool", "content": CONCLUSION_TEXT}]}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"]["cause"] == "no_conclusion"


def test_a_graph_that_ends_in_prose_has_no_conclusion(tmp_path):
    agent = _agent(tmp_path, _graph_returning({"messages": []}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"]["cause"] == "no_conclusion"
    assert report["final_response"] is None


def test_a_provider_error_is_a_model_error_with_its_text(tmp_path):
    def broken(tools):
        raise RuntimeError("litellm.APIConnectionError: Connection refused " + "x" * 300)
    agent = _agent(tmp_path, _graph_returning(broken))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    termination = report["execution"]["termination"]
    assert termination["cause"] == "model_error"
    assert len(termination["detail"]) <= 200


def test_a_dry_run_says_so(tmp_path):
    agent = _agent(tmp_path, _graph_returning({}), dry_run=True)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"] == {
        "cause": "dry_run", "detail": "dry-run does not execute ANI operations", "turn": 0}


def test_litellm_provider_banner_is_silenced_and_the_client_is_unchanged(tmp_path, monkeypatch):
    import contextlib
    import io

    import litellm

    monkeypatch.setattr(litellm, "suppress_debug_info", False)
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/fr-gpt-5.4", artifact_directory=str(tmp_path)),
        ani=FakeANI())
    model = agent._model()
    assert litellm.suppress_debug_info is True
    assert (model.model, model.model_kwargs) == ("openai/fr-gpt-5.4", {})
    # The lookup that printed the banner still misses, as it did; it only stays quiet.
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed), pytest.raises(litellm.exceptions.BadRequestError):
        litellm.get_llm_provider("fr-gpt-5.4")
    assert "Provider List" not in printed.getvalue()


class BudgetANI(FakeANI):
    """A FakeANI that owns a budget the way the platform's ANI does."""

    def __init__(self):
        self.call_budget = None
        self.calls_made = 0
        self.calls_refused = 0

    def dispatch(self, operation, arguments, *, task=None):
        self.calls_made += 1
        return super().dispatch(operation, arguments, task=task)

    def budget_state(self):
        return {"limit": self.call_budget, "used": self.calls_made, "refused": self.calls_refused,
                "reached": self.call_budget is not None and self.calls_made >= self.call_budget}


def test_the_ani_count_starts_at_zero_for_every_episode(tmp_path):
    # One subject process serves many episodes; the ANI it holds outlives each of
    # them, so the count it reports must be the episode's, not the process's.
    def two_calls(tools):
        tools["get_state"].invoke({"nodes": ["leaf1"]})
        tools["execute_validation"].invoke({"checks": [{"type": "public_success_criteria"}]})
        return {"messages": [{"role": "assistant", "content": '{"status": "completed", "summary": "done"}'}]}
    ani = BudgetANI()
    agent = _agent(tmp_path, _graph_returning(two_calls), ani=ani)
    first = json.loads(agent.invoke(json.dumps(TASK)))
    second = json.loads(agent.invoke(json.dumps(TASK)))
    assert first["execution"]["interaction_limits"]["ani"]["used"] == 2
    assert second["execution"]["interaction_limits"]["ani"]["used"] == 2, "the second episode counted from zero"


# --- request_extra_body: a gateway's provider pin travels in extra_body and is recorded ---

def test_request_extra_body_is_merged_into_extra_body_and_recorded(tmp_path):
    pin = '{"provider": {"order": ["deepinfra"], "allow_fallbacks": false, "quantizations": ["bf16"]}}'
    config = LangChainAgentConfig(model="openai/qwen/qwen3.8-27b", api_key="k", api_base="https://openrouter.ai/api/v1",
                                  enable_thinking=True, artifact_directory=str(tmp_path), request_extra_body=pin)
    agent = LangChainRepairAgent(config, ani=FakeANI(), agent_factory=lambda **kwargs: None)
    kwargs = agent._model_kwargs()
    assert kwargs["extra_body"]["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False, "quantizations": ["bf16"]}
    assert kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}, "the thinking flag survives the merge"


def test_request_extra_body_that_is_not_a_json_object_is_refused(tmp_path):
    import pytest
    config = LangChainAgentConfig(artifact_directory=str(tmp_path), request_extra_body='["not", "an", "object"]')
    agent = LangChainRepairAgent(config, ani=FakeANI(), model=object(), agent_factory=lambda **kwargs: None)
    with pytest.raises(ValueError, match="request_extra_body"):
        agent._model_kwargs()


def test_request_extra_body_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_AGENT_REQUEST_EXTRA_BODY", '{"provider": {"order": ["deepinfra"]}}')
    assert LangChainAgentConfig.from_env().request_extra_body == '{"provider": {"order": ["deepinfra"]}}'


# --- submit_result ends the real langchain graph ---------------------------------------

class ScriptedChatModel:
    """Builds a langchain fake chat model answering with the given AI messages in turn."""

    @staticmethod
    def build(*messages):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        class Model(GenericFakeChatModel):
            calls: int = 0

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, *args, **kwargs):
                self.calls += 1
                return super()._generate(*args, **kwargs)

        return Model(messages=iter(messages))


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class CountingANI(FakeANI):
    def __init__(self):
        self.operations = []

    def dispatch(self, operation, arguments, *, task=None):
        self.operations.append(operation)
        return super().dispatch(operation, arguments, task=task)


def test_the_graph_stops_after_submit_result_without_another_model_call(tmp_path):
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel.build(
        AIMessage(content="", tool_calls=[_call("execute_validation", {"checks": [{"type": "public_success_criteria"}]}, "c1")]),
        AIMessage(content="", tool_calls=[_call("submit_result", {"status": "completed", "summary": "validated"}, "c2")]),
        AIMessage(content="", tool_calls=[_call("get_state", {"nodes": ["leaf1"]}, "c3")]),
    )
    ani = CountingANI()
    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=ani, model=model)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert model.calls == 2, "the middleware ended the graph before a third model call"
    assert ani.operations == ["execute_validation", "submit_result"]
    assert report["status"] == "completed" and report["verified"] is True
    assert report["execution"]["termination"]["via"] == "submit_result"


def test_a_submission_beside_another_call_still_ends_the_graph(tmp_path):
    from langchain_core.messages import AIMessage

    # The first `failed`, this early, is sent back (`_early_failure_pushback`): the second
    # is the submission, beside another call.
    model = ScriptedChatModel.build(
        AIMessage(content="", tool_calls=[_call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c0")]),
        AIMessage(content="", tool_calls=[
            _call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c1"),
            _call("get_state", {"nodes": ["leaf1"]}, "c2")]),
        AIMessage(content="", tool_calls=[_call("get_state", {"nodes": ["leaf1"]}, "c3")]),
    )
    ani = CountingANI()
    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=ani, model=model)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert model.calls == 2
    assert ani.operations == ["submit_result"], "the get_state beside it was not sent"
    assert report["status"] == "failed"
    assert report["execution"]["conclusion_source"] == "submit_result"


def test_calls_beside_a_submission_run_in_the_order_the_model_listed_them(tmp_path):
    """`create_agent` runs the calls of one model message at the same time, so which of a
    submission and the call beside it reached the ANI first was a matter of timing: the
    call listed after `submit_result` was sent in about half of the runs, and a call listed
    before it was refused in about half. Such a message now runs one call at a time in the
    listed order. Repeated, because one run of a race proves nothing."""
    from langchain_core.messages import AIMessage

    for attempt in range(15):
        # Listed after the submission: not sent.
        model = ScriptedChatModel.build(
            AIMessage(content="", tool_calls=[_call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c0")]),
            AIMessage(content="", tool_calls=[
                _call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c1"),
                _call("get_state", {"nodes": ["leaf1"]}, "c2")]),
        )
        ani = CountingANI()
        agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path / f"a{attempt}")), ani=ani, model=model)
        report = json.loads(agent.invoke(json.dumps(TASK)))
        assert ani.operations == ["submit_result"], (attempt, ani.operations)
        assert report["execution"]["conclusion_source"] == "submit_result"

        # Listed before the submission: sent, and before it.
        model = ScriptedChatModel.build(
            AIMessage(content="", tool_calls=[_call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c0")]),
            AIMessage(content="", tool_calls=[
                _call("get_state", {"nodes": ["leaf1"]}, "c1"),
                _call("get_state", {"nodes": ["leaf2"]}, "c2"),
                _call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c3")]),
        )
        ani = CountingANI()
        agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path / f"b{attempt}")), ani=ani, model=model)
        report = json.loads(agent.invoke(json.dumps(TASK)))
        assert ani.operations == ["get_state", "get_state", "submit_result"], (attempt, ani.operations)
        assert report["status"] == "failed" and model.calls == 2


def test_calls_after_the_submission_are_not_sent(tmp_path):
    from sut.langchain_agent.ani_tools import LangChainANIAdapter

    ani = CountingANI()
    adapter = LangChainANIAdapter(ani, task=TASK, artifact_directory=tmp_path)
    tools = {item.name: item for item in adapter.tools()}
    tools["submit_result"].invoke({"status": "failed", "summary": "no safe repair"})
    answer = json.loads(tools["get_state"].invoke({"nodes": ["leaf1"]}))
    assert ani.operations == ["submit_result"]
    assert answer["ok"] is False and "submit_result" in answer["error"]
    assert adapter.trace.submission == {"status": "failed", "summary": "no safe repair", "diagnosis": None}


def test_the_task_names_the_episodes_tools(tmp_path):
    """`tools` in the first message names the tools the episode is given (2026-10-28)."""
    from benchmarks.platforms.containerlab import ANI_OPERATIONS

    captured = {}

    class RecordingGraph(FakeGraph):
        async def ainvoke(self, state, config=None):
            captured["state"] = state
            return await super().ainvoke(state, config)

    def factory(**kwargs):
        captured["tools"] = [tool.name for tool in kwargs["tools"]]
        return RecordingGraph(kwargs["tools"])

    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=FakeANI(),
                                 model=object(), agent_factory=factory)
    agent.invoke(json.dumps({**TASK, "tools": list(ANI_OPERATIONS)}))
    message = captured["state"]["messages"][0]
    task = json.loads(message["content"] if isinstance(message, dict) else message.content)
    assert task["tools"] == captured["tools"] and set(task["tools"]) <= set(ANI_OPERATIONS)


def test_a_reply_with_no_call_and_no_conclusion_is_nudged_and_the_episode_goes_on(tmp_path):
    """E19 disable_routing.m1, drop_traffic.m3, wrong_routing_table.m3: a reply that
    thought in its content and stopped ended the graph with no conclusion."""
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel.build(
        AIMessage(content="web-dmz's default route points at the firewall, which forwards; "
                          "the fault must be on the firewall's forward chain."),
        # Twice: the first `failed`, this early, is sent back (`_early_failure_pushback`).
        AIMessage(content="", tool_calls=[_call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c1"),
                                          _call("submit_result", {"status": "failed", "summary": "no safe repair"}, "c2")]),
    )
    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=CountingANI(), model=model)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert model.calls == 2, "the text reply was sent back, not the end"
    assert report["execution"]["termination"]["via"] == "submit_result"
    nudges = report["execution"]["no_action_nudges"]
    assert len(nudges) == 1 and nudges[0]["operations_before"] == 0 and nudges[0]["reply_chars"] > 0


def test_a_tool_call_written_as_text_is_named_in_the_nudge(tmp_path):
    """E20 wrong_routing_table.m3: 343 turns of `<tool_call>` markup the
    provider's parser did not read, each answered with the generic nudge."""
    from langchain_core.messages import AIMessage
    from sut.common.messages import UNPARSED_TOOL_CALL_NOTE

    seen = []

    class Model(type(ScriptedChatModel.build())):
        def _generate(self, messages, *args, **kwargs):
            seen.append(messages[-1].content)
            return super()._generate(messages, *args, **kwargs)

    model = Model(messages=iter([
        AIMessage(content='<tool_call>\n<function=get_state(nodes=["leaf1"])\n</function>\n</tool_call>'),
        AIMessage(content='{"status": "failed", "summary": "no safe repair"}'),
    ]))
    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=CountingANI(), model=model)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert seen[1].startswith(UNPARSED_TOOL_CALL_NOTE)
    assert [nudge["unparsed_tool_call"] for nudge in report["execution"]["no_action_nudges"]] == [True]


def test_a_conclusion_written_as_json_text_ends_the_graph_without_a_nudge(tmp_path):
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel.build(
        AIMessage(content='{"status": "failed", "summary": "no safe repair"}'),
        AIMessage(content="", tool_calls=[_call("get_state", {"nodes": ["leaf1"]}, "c1")]),
    )
    agent = LangChainRepairAgent(LangChainAgentConfig(artifact_directory=str(tmp_path)), ani=CountingANI(), model=model)
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert model.calls == 1
    assert report["execution"]["conclusion_source"] == "final_message"
    assert report["execution"]["no_action_nudges"] == []
