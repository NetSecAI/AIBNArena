from __future__ import annotations

import json
import asyncio

import pytest

from sut.common.thinking import ThinkingUnsupported
from sut.langchain_agent.agent import LangChainAgentConfig, LangChainRepairAgent, StructuredConclusion


class FakeANI:
    def dispatch(self, operation, arguments, *, task=None):
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
        return {"structured_response": StructuredConclusion(status="completed", summary="validated"), "messages": [{"role": "assistant", "usage_metadata": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}, "response_metadata": {"model_name": "provider/langchain-model"}}]}


def test_create_agent_factory_and_structured_result_are_used(tmp_path):
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
    assert captured["response_format"].schema is StructuredConclusion
    assert captured["graph"].invocation_config["recursion_limit"] == 1000
    assert report["ani_operations"][0]["arguments"]["changes"][0]["target"] == "leaf1"
    # The loop's own keys for how the episode ended, so the summary reads this
    # subject's end from the name it gives.
    assert report["final_response"] == {"status": "completed", "summary": "validated"}
    assert report["execution"]["termination"] == {"cause": "own_conclusion", "detail": "completed", "turn": 1}
    assert report["execution"]["interaction_limits"]["ani"]["limit"] is None
    assert set(report["execution"]["interaction_limits"]) == {"ani"}


def test_wall_clock_budget_cancels_the_graph(tmp_path):
    class SlowGraph:
        async def ainvoke(self, state, config=None):
            await asyncio.sleep(1)
            return {"structured_response": StructuredConclusion(status="completed", summary="late")}

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
    """langchain's ToolStrategy binds with tool_choice="any"; under vLLM that is a JSON
    grammar after the reasoning block, and a model that never closes its <think> block
    (Qwen3.5-9B, most turns after a tool result) then returns finish=tool_calls with an
    empty list and no content, ending the graph on turn two. The subject binds "auto"
    unless told otherwise, and says so in the record."""
    captured = {}
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen35-9b-think", api_key="k",
                             enable_thinking=True, artifact_directory=str(tmp_path)),
        ani=FakeANI(), agent_factory=_capturing_factory(captured))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    model = captured["model"]
    for forced in ("any", "required"):
        bound = model.bind_tools([{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
                                 tool_choice=forced)
        assert bound.kwargs["tool_choice"] == "auto", forced
    named = model.bind_tools([{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}],
                             tool_choice="none")
    assert named.kwargs["tool_choice"] == "none", "only the forcing values are replaced"
    assert model.model_kwargs == {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}, \
        "the thinking request still travels with the relaxed binding"
    assert report["execution"]["model_parameters"]["tool_choice"] == "auto"


def test_required_keeps_langchains_forced_tool_choice(tmp_path):
    agent = LangChainRepairAgent(
        LangChainAgentConfig(model="openai/qwen35-9b-think", api_key="k", tool_choice="required",
                             artifact_directory=str(tmp_path)),
        ani=FakeANI())
    bound = agent._model().bind_tools(
        [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}], tool_choice="any")
    assert bound.kwargs["tool_choice"] in ("any", "required")


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


def test_a_self_declared_failure_is_its_own_conclusion(tmp_path):
    agent = _agent(tmp_path, _graph_returning(
        {"structured_response": StructuredConclusion(status="failed", summary="no safe repair")}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["status"] == "failed"
    assert report["error"] == "no safe repair", "the loop files a self-declared failure under error"
    assert report["final_response"] == {"status": "failed", "summary": "no safe repair"}
    assert report["execution"]["termination"]["cause"] == "own_conclusion"


def test_a_completion_the_gate_refused_is_named_as_refused(tmp_path):
    agent = _agent(tmp_path, _graph_returning(
        {"structured_response": StructuredConclusion(status="completed", summary="done")}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["status"] == "failed"
    assert report["final_response"]["status"] == "completed", "what the subject claimed"
    assert report["execution"]["termination"]["cause"] == "completion_rejected"
    assert "without a passing public validation" in report["execution"]["termination"]["detail"]


def test_the_repeated_failure_stop_rule_is_named_not_filed_as_a_model_error(tmp_path):
    def three_failed_reads(tools):
        for _ in range(3):
            tools["get_state"].invoke({"nodes": ["leaf1"]})
        return {"structured_response": StructuredConclusion(status="failed", summary="x")}
    agent = _agent(tmp_path, _graph_returning(three_failed_reads), ani=FailingANI())
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["termination"]["cause"] == "repeated_failed_requests"
    assert report["execution"]["termination"]["detail"].startswith("stopped after 3 identical failed")
    assert report["final_response"] is None


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


def test_a_structured_conclusion_says_where_it_came_from(tmp_path):
    agent = _agent(tmp_path, _graph_returning(
        {"structured_response": StructuredConclusion(status="failed", summary="no safe repair")}))
    report = json.loads(agent.invoke(json.dumps(TASK)))
    assert report["execution"]["conclusion_source"] == "structured_response"


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
        return {"structured_response": StructuredConclusion(status="completed", summary="done")}
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
