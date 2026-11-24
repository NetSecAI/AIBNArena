"""Golden tests pinning the ANI self_execute loop.

The loop is the one piece every ANI SUT must share byte for byte: if two SUTs
disagree on the completion gate, on how ANI results become `actions[]`, or on
how the budget is spent, their benchmark metrics stop being comparable and the
difference is silently attributed to the agent instead of to the plumbing.

The tests drive an agent through its public `invoke()` with a fake ANI and a
scripted model, so they need no lab, no provider and no server. `AGENTS` lists
every SUT that claims to implement the contract; each one is held to the same
goldens.

Run: python sut/common/tests/test_self_execute_loop.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import ANI_VERSION, ANIRequestError  # noqa: E402
from sut.common import (  # noqa: E402
    ANSWER_CONTRACT,
    FINAL_STATUS_CONTRACT,
    ModelAgentConfig,
    SelfExecuteRuntime,
    Tracer,
    execution_budget,
    install_ani_budget,
    invoke,
)


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #

UPDATE_CONFIG_RESULT = {
    "ok": True,
    "operation": "update_config",
    "transaction_id": "ani_tx_test",
    "changes": [
        {
            "target": "leaf2",
            "commands": ["set / interface ethernet-1/40 admin-state enable"],
            "reason": "restore the access link",
            "result": {"ok": True, "safe": True, "target": "leaf2", "returncode": 0},
        }
    ],
}

GET_STATE_RESULT = {"ok": True, "operation": "get_state", "records": []}


def validation_result(passed: bool) -> dict[str, Any]:
    return {
        "ok": passed,
        "operation": "execute_validation",
        "checks": [{"type": "public_success_criteria", "passed": passed, "checks": []}],
    }


class FakeANI:
    """Duck-typed stand-in for ContainerLabANI: canned results, recorded calls."""

    def __init__(self, results: dict[str, Any] | None = None, raises: dict[str, Exception] | None = None):
        self.results = results or {}
        self.raises = raises or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": name}} for name in sorted(self.results)]

    def dispatch(self, operation: str, arguments: dict[str, Any] | None, *, task: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((operation, arguments or {}))
        if operation in self.raises:
            raise self.raises[operation]
        return self.results.get(operation, {"ok": True, "operation": operation})


class ScriptedModel:
    """Replays a fixed list of model messages, repeating the last one forever.

    Repeating matters for the completion-gate case: the loop must keep asking
    until the budget runs out, exactly as a stubborn model would behave.
    """

    def __init__(self, messages: list[dict[str, Any]], *, delay: float = 0.0, raises: Exception | None = None):
        self.messages = messages
        self.delay = delay
        self.raises = raises
        self.call_count = 0
        self.seen_messages: list[Any] = []

    def __call__(self, messages: list[dict[str, Any]], remaining_seconds: float) -> Any:
        self.call_count += 1
        # The transcript as the model saw it, so a test can check that a tool response
        # answers the id the assistant turn actually used.
        self.seen_messages = [dict(message) if isinstance(message, dict) else message
                              for message in messages]
        if self.raises is not None:
            raise self.raises
        if self.delay:
            time.sleep(self.delay)
        index = min(self.call_count - 1, len(self.messages) - 1)
        return self.messages[index]


def tool_message(name: str, arguments: Any = None, *, call_id: str | None = None) -> dict[str, Any]:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": call_id or f"call_{name}",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }


def final_message(status: str, summary: str = "done") -> dict[str, Any]:
    return {"content": json.dumps({"status": status, "summary": summary}), "tool_calls": None}


TASK = {
    "intent": "Restore the intended IPv4 connectivity.",
    "execution_budget": {"wall_clock_seconds": 30},
    "success_criteria": {"all_of": [{"type": "lab_connectivity"}]},
    "observation": {"connectivity": []},
}


# --------------------------------------------------------------------------- #
# Agents under test
# --------------------------------------------------------------------------- #

class LoopAgent:
    """The shared loop with nothing of a subject's own around it.

    What every SUT built on the loop supplies -- a config, the two prompts carrying
    the contracts the loop parses, one tracer for the agent's life, the ANI budget
    installed on the ANI -- and nothing else, so what these tests pin is the loop.
    """

    def __init__(self, config: ModelAgentConfig, ani: FakeANI):
        self.config = config
        self.ani = ani
        self.trace = Tracer(config.debug_trace, config.trace_directory)
        install_ani_budget(ani, config.ani_call_limit)

    def invoke(self, task_json: str) -> str:
        return invoke(self._runtime(), task_json)

    def _execution_budget(self, task: dict[str, Any]) -> float:
        return execution_budget(task, self.config.max_execution_seconds)

    def _runtime(self) -> SelfExecuteRuntime:
        # call_model is resolved here, so a test can substitute it on the instance.
        return SelfExecuteRuntime(
            ani=self.ani,
            call_model=self._call_model,
            system_prompt=f"Repair the network through the ANI tools.\n\n{FINAL_STATUS_CONTRACT}",
            question_system_prompt=f"Answer from what the ANI reports.\n\n{ANSWER_CONTRACT}",
            max_execution_seconds=self.config.max_execution_seconds,
            trace=self.trace,
            dry_run=self.config.dry_run,
            self_execute=self.config.self_execute,
            sut_identity="shared_loop",
            configured_model=self.config.model,
            model_parameters={"model": self.config.model,
                              "ani_call_limit": self.config.ani_call_limit,
                              "keep_recent_reads": self.config.keep_recent_reads,
                              "keep_recent_read_chars": self.config.keep_recent_read_chars},
            ani_call_limit=self.config.ani_call_limit,
            tool_result_chars=self.config.tool_result_chars,
            context_budget_chars=self.config.context_budget_chars,
            artifact_directory=self.config.artifact_directory,
            max_consecutive_rejections=self.config.max_consecutive_rejections,
            keep_recent_reads=self.config.keep_recent_reads,
            keep_recent_read_chars=self.config.keep_recent_read_chars,
        )


def _loop_agent(config_overrides: dict[str, Any], ani: FakeANI, model: ScriptedModel):
    agent = LoopAgent(ModelAgentConfig(**config_overrides), ani)
    agent._call_model = model
    return agent


AGENTS = [("shared_loop", _loop_agent)]


def run_invoke(builder, *, config: dict[str, Any], ani: FakeANI, model: ScriptedModel, task: dict[str, Any] | None = None) -> dict[str, Any]:
    agent = builder(config, ani, model)
    return json.loads(agent.invoke(json.dumps(task if task is not None else TASK)))


def scrub(report: dict[str, Any]) -> dict[str, Any]:
    """Drop the wall-clock fields so reports compare by shape and content."""
    scrubbed = json.loads(json.dumps(report))
    execution = scrubbed.get("execution")
    if isinstance(execution, dict):
        execution.pop("elapsed_seconds", None)
    for operation in scrubbed.get("ani_operations") or []:
        operation.pop("duration_seconds", None)
    return scrubbed


# --------------------------------------------------------------------------- #
# Goldens
# --------------------------------------------------------------------------- #

def test_happy_path_reports_a_verified_action():
    for name, builder in AGENTS:
        ani = FakeANI({
            "get_state": GET_STATE_RESULT,
            "update_config": UPDATE_CONFIG_RESULT,
            "execute_validation": validation_result(True),
        })
        model = ScriptedModel([
            tool_message("get_state"),
            tool_message("update_config"),
            tool_message("execute_validation"),
            final_message("completed"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["mode"] == "self_execute", name
        assert report["status"] == "completed", name
        assert report["verified"] is True, name
        assert report["error"] is None, name
        assert report["execution"]["ani_version"] == ANI_VERSION, name
        assert report["execution"]["tool_call_count"] == 3, name
        assert report["execution"]["budget_seconds"] == 30.0, name
        assert len(report["device_changes"]) == 1, name

        action = report["device_changes"][0]
        assert action["operation"] == "update_config", name
        assert action["transaction_id"] == "ani_tx_test", name
        assert action["action"]["machine"] == "leaf2", name
        assert action["action"]["command"] == "set / interface ethernet-1/40 admin-state enable", name
        assert action["result"]["ok"] is True, name
        assert action["verified_after_action"] is True, name
        assert action["objective_verification"]["passed"] is True, name
        assert report["final_observation"]["type"] == "public_success_criteria", name
        assert [operation["operation"] for operation in report["ani_operations"]] == [
            "get_state", "update_config", "execute_validation",
        ], name


def test_completion_without_passing_validation_is_rejected_and_keeps_its_error():
    """The gate must survive to the end: the timeout fallback must not overwrite it."""
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(False)})
        model = ScriptedModel([final_message("completed")], delay=0.05)
        report = scrub(run_invoke(
            builder,
            config={"max_execution_seconds": 1.0},
            ani=ani,
            model=model,
            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}},
        ))

        assert report["status"] == "timeout", name
        assert report["verified"] is False, name
        assert report["error"] == "SUT claimed completion without a passing public validation", name
        assert model.call_count > 1, f"{name}: the loop must keep asking after a rejected completion"


def test_failed_validation_does_not_verify_a_completed_status():
    for name, builder in AGENTS:
        ani = FakeANI({
            "update_config": UPDATE_CONFIG_RESULT,
            "execute_validation": validation_result(False),
        })
        model = ScriptedModel([
            tool_message("update_config"),
            tool_message("execute_validation"),
            final_message("failed", "no safe repair found"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "failed", name
        assert report["verified"] is False, name
        assert report["error"] == "no safe repair found", name
        assert report["device_changes"][0]["verified_after_action"] is False, name
        assert "objective_verification" not in report["device_changes"][0], name


def test_any_later_network_mutation_consumes_a_passing_validation():
    """Completion needs a validation of the final state, not an earlier state."""
    for operation in (
        "update_config",
        "rollback_config",
    ):
        for name, builder in AGENTS:
            mutation = dict(UPDATE_CONFIG_RESULT)
            mutation["operation"] = operation
            ani = FakeANI({
                "execute_validation": validation_result(True),
                operation: mutation,
            })
            model = ScriptedModel([
                tool_message("execute_validation"),
                tool_message(operation),
                final_message("completed"),
                final_message("failed", "final state was not revalidated"),
            ])
            report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

            assert report["status"] == "failed", (operation, name)
            assert report["verified"] is False, (operation, name)
            assert report["final_observation"] is None, (operation, name)
            assert report["error"] == "final state was not revalidated", (operation, name)
            assert model.call_count == 4, (operation, name)


def test_validation_after_the_last_mutation_can_complete():
    for name, builder in AGENTS:
        ani = FakeANI({
            "update_config": UPDATE_CONFIG_RESULT,
            "execute_validation": validation_result(True),
        })
        model = ScriptedModel([
            tool_message("execute_validation"),
            tool_message("update_config"),
            tool_message("execute_validation"),
            final_message("completed"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "completed", name
        assert report["verified"] is True, name
        assert report["final_observation"]["passed"] is True, name


def test_a_call_refused_before_any_tool_keeps_a_passing_validation():
    """It reaches no device, so the validation still describes the network the episode
    ends on. Until 2026-11-15 it consumed it here, where langchain_agent's adapter never
    sees a call refused before any tool."""
    refused = {"ok": False, "operation": "update_config", "dispatched": False, "error": "refused"}
    for operation, arguments, answer in (
        ("update_config", {"changes": []}, refused),
    ):
        for name, builder in AGENTS:
            ani = FakeANI({"execute_validation": validation_result(True), operation: answer})
            model = ScriptedModel([
                tool_message("execute_validation"),
                tool_message(operation, arguments),
                final_message("completed"),
            ])
            report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

            assert report["status"] == "completed", (operation, arguments, name)
            assert report["verified"] is True, (operation, arguments, name)
            assert report["final_observation"]["passed"] is True, (operation, arguments, name)


def test_a_failed_action_is_never_credited_with_a_passing_validation():
    """Observed for real: an action reported ok=False carried verified_after_action=True.

    The change had not landed the way the report claimed, yet the action read as the
    one that fixed the network. A passing objective still shows up in
    final_observation — it is only the per-action credit that has to be earned.
    """
    failed_update = {
        **UPDATE_CONFIG_RESULT,
        "changes": [{
            **UPDATE_CONFIG_RESULT["changes"][0],
            "result": {"ok": False, "safe": True, "target": "leaf2", "returncode": 1},
        }],
    }
    for name, builder in AGENTS:
        ani = FakeANI({"update_config": failed_update, "execute_validation": validation_result(True)})
        model = ScriptedModel([
            tool_message("update_config"),
            tool_message("execute_validation"),
            final_message("completed"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        action = report["device_changes"][0]
        assert action["result"]["ok"] is False, name
        assert action["verified_after_action"] is False, f"{name}: a failed action must not be credited"
        assert "objective_verification" not in action, name
        # The validation did pass, and the report still says so at the top level.
        assert report["final_observation"]["passed"] is True, name
        assert report["verified"] is True, name


def test_ani_request_error_is_reported_but_not_fatal():
    for name, builder in AGENTS:
        ani = FakeANI(
            {"get_state": GET_STATE_RESULT},
            raises={"update_config": ANIRequestError("update_config requires a non-empty changes list")},
        )
        model = ScriptedModel([
            tool_message("update_config"),
            final_message("failed", "gave up"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "failed", name
        assert report["device_changes"] == [], name
        assert report["ani_operations"][0]["ok"] is False, name
        assert "non-empty changes list" in report["ani_operations"][0]["error"], name


def test_unexpected_ani_read_exception_stays_inside_the_report_contract():
    for name, builder in AGENTS:
        ani = FakeANI(raises={"get_state": RuntimeError("transport vanished")})
        model = ScriptedModel([
            tool_message("get_state"),
            final_message("failed", "could not observe the lab"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "failed", name
        assert report["device_changes"] == [], name
        assert report["ani_operations"][0]["ok"] is False, name
        assert report["ani_operations"][0]["error"] == (
            "RuntimeError: transport vanished"), name


def test_unexpected_writer_exception_consumes_old_validation():
    for name, builder in AGENTS:
        ani = FakeANI(
            {"execute_validation": validation_result(True)},
            raises={"update_config": RuntimeError("lost writer response")},
        )
        model = ScriptedModel([
            tool_message("execute_validation"),
            tool_message("update_config"),
            final_message("completed"),
            final_message("failed", "writer outcome needs reconciliation"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "failed", name
        assert report["verified"] is False, name
        assert report["final_observation"] is None, name
        assert report["device_changes"] == [], name
        assert report["ani_operations"][-1]["error"] == (
            "RuntimeError: lost writer response"), name
        assert report["ani_operations"][-1]["indeterminate"] is True, name
        assert model.call_count == 4, name


def test_model_exception_fails_the_episode():
    for name, builder in AGENTS:
        ani = FakeANI({})
        model = ScriptedModel([], raises=RuntimeError("provider exploded"))
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert report["status"] == "failed", name
        assert report["error"] == "provider exploded", name
        assert report["device_changes"] == [], name
        assert report["execution"]["tool_call_count"] == 0, name


def test_a_tool_call_with_an_empty_argument_string_is_a_call_with_no_argument():
    """A provider serialises a call of a tool that takes no argument as an empty string
    (vLLM does). That is the empty object, not broken JSON: in main04 an episode ended at
    turn 60 on such a call, after the same tool had run 112 times with `{}`. Broken JSON
    is still refused."""
    from sut.common.messages import ModelActionError, tool_call_parts

    for blank in ("", "   ", "\n"):
        call = {"id": "c1", "type": "function",
                "function": {"name": "get_topology", "arguments": blank}}
        assert tool_call_parts(call) == ("get_topology", {}, "c1"), repr(blank)
    try:
        tool_call_parts({"id": "c2", "function": {"name": "get_state", "arguments": "{"}})
    except ModelActionError as exc:
        assert "invalid JSON arguments" in str(exc)
    else:
        raise AssertionError("broken JSON arguments must still be refused")


def test_malformed_tool_call_is_rejected_inside_the_loop():
    malformed = [
        {"content": None, "tool_calls": [
            {"id": "c1", "function": {"arguments": "{}"}}]},
        {"content": None, "tool_calls": [
            {"id": "c2", "function": {"name": "get_state", "arguments": "{"}}]},
    ]
    for name, builder in AGENTS:
        for broken in malformed:
            ani = FakeANI({})
            model = ScriptedModel([
                broken,
                final_message("failed", "stopped safely after malformed call"),
            ])
            report = run_invoke(builder, config={}, ani=ani, model=model)

            assert report["mode"] == "self_execute", name
            assert report["status"] == "failed", name
            assert report["error"] == "stopped safely after malformed call", name
            assert report["execution"]["tool_call_count"] == 0, name
            assert model.call_count == 2, name
            assert ani.calls == [], f"{name}: malformed calls must never reach ANI"


def test_question_task_returns_an_answer_with_evidence():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = ScriptedModel([
            tool_message("get_state"),
            {"content": json.dumps({"answer": "leaf2 is up"}), "tool_calls": None},
        ])
        agent = builder({}, ani, model)
        payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "is leaf2 up?"})))

        assert payload["answer"] == "leaf2 is up", name
        assert payload["evidence"] == ["ANI get_state: ok=True"], name
        assert "mode" not in payload, f"{name}: a sanity answer is not a self_execute report"


def test_question_task_recovers_from_a_malformed_tool_call():
    broken = {"content": None, "tool_calls": [
        {"id": "c1", "function": {"arguments": "{}"}}]}
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = ScriptedModel([
            broken,
            tool_message("get_state"),
            {"content": json.dumps({"answer": "leaf2 is up"}), "tool_calls": None},
        ])
        agent = builder({}, ani, model)
        payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "is leaf2 up?"})))

        assert payload["answer"] == "leaf2 is up", name
        assert payload["evidence"] == ["ANI get_state: ok=True"], name
        assert model.call_count == 3, name
        assert [call[0] for call in ani.calls] == ["get_state"], name


def test_question_task_never_dispatches_mutating_tools():
    for operation in (
        "update_config",
        "update_object",
        "rollback_config",
    ):
        for name, builder in AGENTS:
            ani = FakeANI({operation: UPDATE_CONFIG_RESULT})
            model = ScriptedModel([
                tool_message(operation),
                {"content": json.dumps({"answer": "blocked safely"}), "tool_calls": None},
            ])
            agent = builder({}, ani, model)
            payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "change it?"})))

            assert payload["answer"] == "blocked safely", (operation, name)
            assert payload["evidence"] == [f"ANI {operation}: ok=False"], (operation, name)
            assert ani.calls == [], (operation, name)


def test_question_read_tools_still_dispatch_normally():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = ScriptedModel([
            tool_message("get_state"),
            {"content": json.dumps({"answer": "observed"}), "tool_calls": None},
        ])
        agent = builder({}, ani, model)
        payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "observe?"})))

        assert payload["answer"] == "observed", name
        assert ani.calls == [("get_state", {})], name


def test_question_read_exception_is_returned_to_the_model_not_the_server():
    for name, builder in AGENTS:
        ani = FakeANI(raises={"get_state": RuntimeError("read timed out")})
        model = ScriptedModel([
            tool_message("get_state"),
            {"content": json.dumps({"answer": "state unavailable"}), "tool_calls": None},
        ])
        agent = builder({}, ani, model)
        payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "observe?"})))

        assert payload["answer"] == "state unavailable", name
        assert payload["evidence"] == ["ANI get_state: ok=False"], name
        assert ani.calls == [("get_state", {})], name


def test_question_task_reports_provider_failure():
    for name, builder in AGENTS:
        ani = FakeANI({})
        model = ScriptedModel([], raises=RuntimeError("provider exploded"))
        agent = builder({}, ani, model)
        payload = json.loads(agent.invoke(json.dumps({**TASK, "question": "is leaf2 up?"})))

        assert payload["answer"] == "Unable to answer because the model provider failed.", name
        assert payload["error"] == "provider exploded", name
        assert payload["evidence"] == [], name


def test_dry_run_and_disabled_self_execute_short_circuit():
    for name, builder in AGENTS:
        ani = FakeANI({})
        model = ScriptedModel([final_message("completed")])

        dry = run_invoke(builder, config={"dry_run": True}, ani=ani, model=model)
        assert dry["mode"] == "self_execute", name
        assert dry["status"] == "failed", name
        assert dry["error"] == "dry-run does not execute ANI operations", name
        assert dry["execution"]["dry_run"] is True, name

        disabled = run_invoke(builder, config={"self_execute": False}, ani=ani, model=model)
        assert disabled["status"] == "failed", name
        assert disabled["error"] == "ANI v0.1 SUT requires self_execute mode", name

        assert model.call_count == 0, f"{name}: neither branch may call the model"


def test_execution_budget_clamps_to_the_configured_cap():
    for name, builder in AGENTS:
        ani = FakeANI({})
        model = ScriptedModel([final_message("failed")])
        agent = builder({"max_execution_seconds": 120.0}, ani, model)
        budget = agent._execution_budget

        assert budget({"execution_budget": {"wall_clock_seconds": 30}}) == 30.0, name
        assert budget({"execution_budget": {"wall_clock_seconds": 999}}) == 120.0, name
        assert budget({}) == 120.0, name
        assert budget({"execution_budget": {"wall_clock_seconds": 0}}) == 120.0, name
        assert budget({"execution_budget": {"wall_clock_seconds": None}}) == 120.0, name
        assert budget({"execution_budget": {"wall_clock_seconds": "abc"}}) == 120.0, name


def test_a_second_episode_runs_and_gets_its_own_trace():
    """One runtime, two episodes. The runtime is a frozen dataclass, so a per-episode
    counter kept on it raised FrozenInstanceError on the first line of every invoke --
    swallowed by the A2A executor into {"error": ...}, which the Judge reads as
    off-contract. The counter belongs to the tracer; this pins that both episodes run
    and that the second does not overwrite the first one's trace."""
    import tempfile

    for name, builder in AGENTS:
        directory = tempfile.mkdtemp()
        ani = FakeANI({"execute_validation": validation_result(True)})
        agent = builder(
            {"debug_trace": "summary", "trace_directory": directory, "model": "m"},
            ani,
            ScriptedModel([tool_message("execute_validation"), final_message("completed")] * 2),
        )

        first = json.loads(agent.invoke(json.dumps(TASK)))
        second = json.loads(agent.invoke(json.dumps(TASK)))

        for episode, report in (("first", first), ("second", second)):
            assert report.get("error") is None, f"{name} {episode} episode: {report['error']}"
            assert report["mode"] == "self_execute", f"{name} {episode} episode went off-contract"

        traces = sorted(Path(directory).glob("trace-*.jsonl"))
        assert len(traces) == 2, f"{name} wrote {len(traces)} trace files for two episodes"


def test_model_and_provider_telemetry_are_reported():
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True)})
        validation_message = tool_message("execute_validation")
        validation_message["_ibn_usage"] = {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}
        validation_message["_ibn_provider_model"] = "provider/model-v1"
        final = final_message("completed")
        final["_ibn_usage"] = {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
        report = run_invoke(builder, config={"model": "configured/model"}, ani=ani, model=ScriptedModel([validation_message, final]))

        assert report["sut_identity"] == name
        assert report["sut_version"] == "1.0.0"
        assert report["model_reported_by_sut"] == "configured/model"
        assert report["provider_reported_model"] == "provider/model-v1"
        assert report["execution"]["llm_calls"] == 2
        assert report["execution"]["token_usage"] == {
            "input_tokens": 18, "output_tokens": 5, "total_tokens": 23,
        }


def test_each_turn_records_the_provider_finish_reason():
    """A reply cut at max_tokens and a reply that chose to make no call both arrive as
    content alone; only the provider's finish_reason tells them apart in the record."""
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True)})
        cut = {"content": "Let me think about the shaper on eth2 and its ceil", "tool_calls": None,
               "_ibn_finish_reason": "length"}
        validation_message = tool_message("execute_validation")
        validation_message["_ibn_finish_reason"] = "tool_calls"
        final = final_message("completed")
        model = ScriptedModel([cut, validation_message, final])
        report = run_invoke(builder, config={}, ani=ani, model=model)

        turns = report["execution"]["model_turns"]
        assert [turn["kind"] for turn in turns] == ["no_action", "tool_calls", "final"], name
        assert [turn["finish_reason"] for turn in turns] == ["length", "tool_calls", None], name
        assert not any("_ibn_finish_reason" in message for message in model.seen_messages
                       if isinstance(message, dict)), f"{name}: telemetry was sent back to the model"


def test_a_tool_call_written_as_text_is_named_in_the_nudge():
    """E20 wrong_routing_table.m3: 343 turns of `<tool_call>` markup the
    provider's parser did not read, each answered with the generic nudge."""
    from sut.common.messages import UNPARSED_TOOL_CALL_NOTE

    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True)})
        written = {"content": "<tool_call>\n<function=execute_validation(checks=[])\n</function>\n</tool_call>",
                   "tool_calls": None}
        prose = {"content": "Let me look at the routes first.", "tool_calls": None}
        model = ScriptedModel([written, prose, tool_message("execute_validation"), final_message("completed")])
        report = run_invoke(builder, config={}, ani=ani, model=model)

        turns = report["execution"]["model_turns"]
        assert [turn["error"] for turn in turns[:2]] == [
            "a tool call written as text, not made", "neither ANI tool calls nor a final status object"], name
        nudges = [message["content"] for message in model.seen_messages
                  if isinstance(message, dict) and message.get("role") == "user"
                  and str(message.get("content")).startswith(("Use an ANI tool next", UNPARSED_TOOL_CALL_NOTE))]
        assert [nudge.startswith(UNPARSED_TOOL_CALL_NOTE) for nudge in nudges] == [True, False], name


def test_a_context_rejection_is_recorded_as_the_cause_of_termination():
    """The provider's context error and a model's own failure both end as "failed".

    Only the cause field tells a run that was cut off from one that concluded, so
    the classifier reads the text the backend gave up retrying on.
    """
    for name, builder in AGENTS:
        model = ScriptedModel([], raises=RuntimeError(
            "litellm.ContextWindowExceededError: This model's maximum context length "
            "is 40960 tokens. However, you requested 41200 tokens."))
        report = run_invoke(builder, config={}, ani=FakeANI({}), model=model)

        termination = report["execution"]["termination"]
        assert report["status"] == "failed", name
        assert termination["cause"] == "context_window", name
        assert termination["turn"] == 1, name
        assert termination["detail"].startswith("litellm.ContextWindowExceededError"), name
        assert len(termination["detail"]) <= 200, name


def test_an_unclassified_model_exception_is_recorded_as_a_model_error():
    for name, builder in AGENTS:
        model = ScriptedModel([], raises=RuntimeError("provider exploded"))
        report = run_invoke(builder, config={}, ani=FakeANI({}), model=model)

        assert report["execution"]["termination"] == {
            "cause": "model_error", "detail": "provider exploded", "turn": 1}, name


def test_completion_rejected_until_the_budget_runs_out_is_named_as_such():
    """A model that keeps claiming completion is not a slow model.

    Both end with status "timeout"; the record has to separate the model that ran
    out of time from the one the gate kept refusing.
    """
    for name, builder in AGENTS:
        model = ScriptedModel([final_message("completed")], delay=0.01)
        report = run_invoke(
            builder,
            config={"max_execution_seconds": 2.0},
            ani=FakeANI({}),
            model=model,
            task={**TASK, "execution_budget": {"wall_clock_seconds": 2}},
        )

        termination = report["execution"]["termination"]
        assert report["status"] == "timeout", name
        assert termination["cause"] == "completion_rejected_until_budget", name
        assert termination["turn"] == len(report["execution"]["model_turns"]), name
        assert termination["detail"] == report["error"], name


def test_a_budget_spent_on_ordinary_turns_is_recorded_as_budget():
    for name, builder in AGENTS:
        # Prose only: every turn is a no_action, none is a rejected completion.
        model = ScriptedModel([{"content": "still thinking", "tool_calls": None}], delay=0.05)
        report = run_invoke(
            builder,
            config={"max_execution_seconds": 1.0},
            ani=FakeANI({}),
            model=model,
            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}},
        )

        assert report["status"] == "timeout", name
        assert report["execution"]["termination"]["cause"] == "budget", name


def test_an_accepted_final_is_recorded_as_the_models_own_conclusion():
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True)})
        model = ScriptedModel([tool_message("execute_validation"), final_message("completed")])
        report = run_invoke(builder, config={}, ani=ani, model=model)
        assert report["execution"]["termination"] == {
            "cause": "own_conclusion", "detail": "completed", "turn": 2}, name

        model = ScriptedModel([final_message("failed", "no safe repair")])
        report = run_invoke(builder, config={}, ani=FakeANI({}), model=model)
        assert report["execution"]["termination"] == {
            "cause": "own_conclusion", "detail": "failed", "turn": 1}, name


def test_a_dry_run_names_itself_as_the_cause():
    for name, builder in AGENTS:
        report = run_invoke(builder, config={"dry_run": True}, ani=FakeANI({}), model=ScriptedModel([]))
        assert report["execution"]["termination"]["cause"] == "dry_run", name
        assert report["execution"]["reservation"] == {
            "reductions": 0, "min_max_tokens": None, "calls_reduced": 0}, name


def test_reservation_reductions_are_summed_over_the_episode():
    """One call reduced twice and one reduced once: three reductions, two calls, and
    the floor is the smallest reservation any reduced call reached."""
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        first = tool_message("get_state")
        first["_ibn_reservation"] = {"reductions": 2, "min_max_tokens": 2000}
        second = tool_message("get_state")
        # A call that kept its ceiling contributes nothing, not a floor of 8000.
        second["_ibn_reservation"] = {"reductions": 0, "min_max_tokens": 8000}
        third = final_message("failed")
        third["_ibn_reservation"] = {"reductions": 1, "min_max_tokens": 4000}
        report = run_invoke(builder, config={}, ani=ani, model=ScriptedModel([first, second, third]))

        assert report["execution"]["reservation"] == {
            "reductions": 3, "min_max_tokens": 2000, "calls_reduced": 2}, name


def test_a_reservation_carried_on_the_exception_is_still_recorded():
    """A call that never returned a message can still have been pushed down first."""
    for name, builder in AGENTS:
        exc = RuntimeError("ContextWindowExceededError: maximum context length is 40960 tokens")
        exc._ibn_reservation = {"reductions": 12, "min_max_tokens": 1}
        report = run_invoke(builder, config={}, ani=FakeANI({}), model=ScriptedModel([], raises=exc))

        assert report["execution"]["termination"]["cause"] == "context_window", name
        assert report["execution"]["reservation"] == {
            "reductions": 12, "min_max_tokens": 1, "calls_reduced": 1}, name


# --- submit_result: the agent's own end, through the ANI -------------------------------

def submission_result(status: str, summary: str = "done", diagnosis: str | None = None) -> dict[str, Any]:
    """What the platform's ANI answers an accepted `submit_result`."""
    return {"ok": True, "operation": "submit_result", "submitted": True,
            "status": status, "summary": summary, "diagnosis": diagnosis}


def test_submit_result_ends_the_episode_as_its_own_conclusion():
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True),
                       "submit_result": submission_result("completed", "enabled e1-40", "leaf2 e1-40 down")})
        model = ScriptedModel([tool_message("execute_validation"), tool_message("submit_result"),
                               tool_message("get_state")])
        report = run_invoke(builder, config={}, ani=ani, model=model)
        assert model.call_count == 2, f"{name}: no model call after the submission"
        assert [call[0] for call in ani.calls] == ["execute_validation", "submit_result"], name
        assert report["status"] == "completed" and report["verified"] is True, name
        assert report["error"] is None, name
        assert report["execution"]["termination"] == {
            "cause": "own_conclusion", "detail": "completed", "turn": 2, "via": "submit_result"}, name
        assert report["final_response"] == {"status": "completed", "summary": "enabled e1-40",
                                            "diagnosis": "leaf2 e1-40 down"}, name
        assert report["diagnosis"]["text"] == "leaf2 e1-40 down", name
        assert report["ani_operations"][-1]["category"] == "submission", name


def test_a_submitted_completion_without_validation_is_kept_and_not_verified():
    """No gate on submit_result: the claim is recorded, `verified` says what held."""
    for name, builder in AGENTS:
        ani = FakeANI({"submit_result": submission_result("completed")})
        model = ScriptedModel([tool_message("submit_result")])
        report = run_invoke(builder, config={}, ani=ani, model=model)
        assert model.call_count == 1, name
        assert report["status"] == "completed", name
        assert report["verified"] is False, name
        assert report["execution"]["termination"]["via"] == "submit_result", name


def test_a_submitted_failure_files_its_summary_as_the_error():
    for name, builder in AGENTS:
        ani = FakeANI({"submit_result": submission_result("failed", "no safe repair")})
        report = run_invoke(builder, config={}, ani=ani, model=ScriptedModel([tool_message("submit_result")]))
        assert report["status"] == "failed", name
        assert report["error"] == "no safe repair", name


def test_calls_after_a_submission_in_the_same_turn_are_not_sent():
    for name, builder in AGENTS:
        ani = FakeANI({"submit_result": submission_result("failed", "no safe repair")})
        turn = {"content": None, "tool_calls": [
            *tool_message("submit_result", call_id="c1")["tool_calls"],
            *tool_message("get_state", call_id="c2")["tool_calls"]]}
        report = run_invoke(builder, config={}, ani=ani, model=ScriptedModel([turn]))
        assert [call[0] for call in ani.calls] == ["submit_result"], name
        assert report["execution"]["termination"]["via"] == "submit_result", name


def test_a_refused_submission_does_not_end_the_episode():
    for name, builder in AGENTS:
        ani = FakeANI(raises={"submit_result": ANIRequestError("submit_result.summary must state ...")})
        model = ScriptedModel([tool_message("submit_result"), final_message("failed", "no safe repair")])
        report = run_invoke(builder, config={}, ani=ani, model=model)
        assert model.call_count == 2, name
        assert report["execution"]["termination"] == {
            "cause": "own_conclusion", "detail": "failed", "turn": 2}, name


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    print(f"agents under test: {', '.join(name for name, _ in AGENTS)}")
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a test runner reports, it does not raise
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_the_loop_reports_the_ani_cap_it_was_given(monkeypatch):
    """The ANI cap is enforced by the ANI, so the loop only reports what was asked for.

    Enforcing it here would bound the calls the model makes by name and leave a subject
    tool free to read and write the devices as often as it liked while the record said
    the budget was spent once. The enforcement itself is covered in
    benchmarks/platforms/containerlab/tests/test_ani_contract.py.
    """
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT, "update_config": UPDATE_CONFIG_RESULT})
        model = ScriptedModel([
            tool_message("get_state"),
            tool_message("update_config"),
            final_message("failed"),
        ])
        report = scrub(run_invoke(
            builder, config={"ani_call_limit": 1}, ani=ani, model=model))

        assert [call[0] for call in ani.calls] == ["get_state", "update_config"], name
        limits = report["execution"]["interaction_limits"]["ani"]
        # The double owns no budget, so the loop reports the request and counts nothing
        # it did not do itself.
        assert limits["limit"] == 1 and limits["used"] is None, name


def test_without_limits_every_call_is_dispatched_and_the_record_says_so():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT, "get_topology": {"ok": True, "operation": "get_topology"}})
        model = ScriptedModel([
            tool_message("get_state"),
            tool_message("get_topology"),
            final_message("failed"),
        ])
        report = scrub(run_invoke(builder, config={}, ani=ani, model=model))

        assert [call[0] for call in ani.calls] == ["get_state", "get_topology"], name
        assert list(report["execution"]["interaction_limits"]) == ["ani"], name
        assert report["execution"]["interaction_limits"]["ani"]["limit"] is None, name


def test_a_tool_call_without_an_id_is_answered_on_the_id_the_turn_used():
    """A provider that sends no id must not leave the tool response unanswerable.

    tool_call_parts mints an id from the clock when the provider sends none, so parsing
    the same call twice minted two: the assistant turn carried one and the tool response
    answered the other, which a provider is entitled to reject.
    """
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        without_id = {"function": {"name": "get_state", "arguments": "{}"}}
        model = ScriptedModel([
            {"content": "", "tool_calls": [without_id]},
            final_message("failed"),
        ])
        run_invoke(builder, config={}, ani=ani, model=model)

        assistant = [m for m in model.seen_messages if m.get("role") == "assistant" and m.get("tool_calls")]
        responses = [m for m in model.seen_messages if m.get("role") == "tool"]
        assert assistant and responses, name
        assert assistant[-1]["tool_calls"][0]["id"] == responses[-1]["tool_call_id"], name


# --------------------------------------------------------------------------- #
# Transcript bounds (all off by default)
# --------------------------------------------------------------------------- #

BIG_STATE = {
    "ok": True,
    "operation": "get_state",
    "records": [{"id": index, "text": "x" * 100} for index in range(120)],
}


def _tool_messages(model: ScriptedModel) -> list[dict[str, Any]]:
    return [message for message in model.seen_messages if message.get("role") == "tool"]


def test_results_travel_whole_by_default_and_the_context_only_counts():
    """No bound set: the tool message is the result, byte for byte, and the record
    says so with zero cuts, zero elisions and the peak size it measured."""
    for name, builder in AGENTS:
        model = ScriptedModel([tool_message("get_state"), final_message("failed")])
        report = run_invoke(builder, config={}, ani=FakeANI({"get_state": BIG_STATE}), model=model)

        (message,) = _tool_messages(model)
        assert json.loads(message["content"]) == BIG_STATE, name
        context = report["execution"]["context"]
        assert context["budget_chars"] is None and context["tool_result_chars"] is None, name
        assert context["max_consecutive_rejections"] is None, name
        assert context["truncated_results"] == 0 and context["elided_results"] == 0, name
        assert context["events"] == [], name
        assert context["peak_prompt_chars"] > len(json.dumps(BIG_STATE)), name


def test_a_tool_result_over_the_limit_is_cut_and_spilled_whole(tmp_path: Path):
    import hashlib

    for name, builder in AGENTS:
        model = ScriptedModel([tool_message("get_state", {"scope": "all"}), final_message("failed")])
        report = run_invoke(
            builder,
            config={"tool_result_chars": 2000, "artifact_directory": str(tmp_path / name)},
            ani=FakeANI({"get_state": BIG_STATE}),
            model=model,
        )

        (message,) = _tool_messages(model)
        envelope = json.loads(message["content"])
        assert envelope["truncated"] is True and envelope["operation"] == "get_state", name
        whole = Path(envelope["artifact_ref"]).read_text(encoding="utf-8")
        assert json.loads(whole) == BIG_STATE, name
        assert envelope["data"] == whole[:2000], name
        assert envelope["sha256"] == hashlib.sha256(whole.encode("utf-8")).hexdigest(), name
        assert envelope["complete_size_chars"] == len(whole), name
        assert "get_state returned" in envelope["note"], name
        context = report["execution"]["context"]
        assert context["truncated_results"] == 1 and context["tool_result_chars"] == 2000, name
        assert context["events"] == [{
            "turn": 1, "kind": "truncated_result", "operation": "get_state",
            "complete_size_chars": len(whole), "sha256": envelope["sha256"],
            "artifact_ref": envelope["artifact_ref"],
        }], name
        # The record still holds the whole result: the cut is only what the model saw.
        assert report["ani_operations"][0]["operation"] == "get_state", name


def test_the_context_budget_elides_superseded_reads_before_the_call(monkeypatch):
    """Six identical reads under a budget that holds two of them. Every tool call
    keeps its answer, the newest answer stays whole, and the call is never made
    over budget."""
    import hashlib

    for name, builder in AGENTS:
        # Sized on this subject's own prompt: what it sends before any read, plus one read
        # and a half, so one read fits and two do not. One figure for every subject had to
        # sit above the longest prompt plus one read and below the shortest plus two, a
        # window of 1 000 characters that closed when the configuration compliance section
        # added 4 088 to every active prompt but not to the archived ones (2026-10-28).
        bare = run_invoke(builder, config={}, ani=FakeANI({"get_state": BIG_STATE}),
                          model=ScriptedModel([final_message("failed")]))
        budget = bare["execution"]["context"]["peak_prompt_chars"] + 3 * len(json.dumps(BIG_STATE)) // 2
        reads = [tool_message("get_state", {"scope": "all"}, call_id=f"read_{index}")
                 for index in range(6)]
        model = ScriptedModel([*reads, final_message("failed")])
        report = run_invoke(
            builder, config={"context_budget_chars": budget},
            ani=FakeANI({"get_state": BIG_STATE}), model=model,
        )

        assert report["status"] == "failed" and report["execution"]["termination"]["cause"] == "own_conclusion", name
        context = report["execution"]["context"]
        assert context["budget_chars"] == budget, name
        assert context["peak_prompt_chars"] <= budget, name
        # One elision before each call from the third on, the final call included: the
        # stub names the read that superseded it at the time, not the last one made.
        assert context["elided_results"] == 5, name
        assert [event["kind"] for event in context["events"]] == ["elided_reads"] * len(context["events"]), name
        seen = model.seen_messages
        asked = [call["id"] for message in seen if message.get("role") == "assistant"
                 for call in message.get("tool_calls") or []]
        answered = {message["tool_call_id"]: message["content"] for message in seen if message.get("role") == "tool"}
        assert asked == [f"read_{index}" for index in range(6)], name
        assert set(asked) == set(answered), name
        assert json.loads(answered["read_5"]) == BIG_STATE, name
        stub = json.loads(answered["read_0"])
        assert stub["superseded"] is True and stub["operation"] == "get_state", name
        assert stub["superseded_by_call"] == "read_1", name
        assert stub["sha256"] == hashlib.sha256(json.dumps(BIG_STATE).encode("utf-8")).hexdigest(), name
        assert json.loads(answered["read_4"])["superseded_by_call"] == "read_5", name


def test_older_reads_become_stubs_and_the_most_recent_stay_whole(monkeypatch):
    """keep_recent_reads: the model is called with the most recent read results whole and
    every older one as a stub naming its call; a validation is never touched. The loop runs
    without it unless the subject sets it (the v2 subjects ship it at 10)."""
    state = {"ok": True, "nodes": ["x" * 400]}
    for name, builder in AGENTS:
        default = run_invoke(builder, config={}, ani=FakeANI({"get_state": state}),
                             model=ScriptedModel([final_message("failed")]))
        assert default["execution"]["context"]["keep_recent_reads"] is None, name
        calls = [tool_message("get_state", {"scope": f"node{index}"}, call_id=f"read_{index}")
                 for index in range(4)]
        calls.insert(2, tool_message("execute_validation", {"checks": [{"type": "public_success_criteria"}]},
                                     call_id="check"))
        model = ScriptedModel([*calls, final_message("failed")])
        report = run_invoke(builder, config={"keep_recent_reads": 2},
                            ani=FakeANI({"get_state": state}), model=model)

        answered = {message["tool_call_id"]: json.loads(message["content"])
                    for message in model.seen_messages if message.get("role") == "tool"}
        for index in (0, 1):
            stub = answered[f"read_{index}"]
            assert stub["elided"] is True and stub["operation"] == "get_state", name
            assert json.loads(stub["arguments"]) == {"scope": f"node{index}"}, "it names its call"
            assert "Call it again" in stub["note"], name
        assert answered["read_2"] == state and answered["read_3"] == state, name
        assert "elided" not in answered["check"], "a validation is never touched"
        context = report["execution"]["context"]
        assert context["elided_results"] == 2 and context["keep_recent_reads"] == 2, name
        assert {event["kind"] for event in context["events"]} == {"elided_older_reads"}, name


def test_older_reads_past_a_character_budget_become_stubs(monkeypatch):
    """keep_recent_read_chars: the most recent read results stay whole as long as they hold
    no more than the budget together, and every older one becomes a stub; a validation is
    neither touched nor counted."""
    state = {"ok": True, "nodes": ["x" * 400]}
    for name, builder in AGENTS:
        calls = [tool_message("get_state", {"scope": f"node{index}"}, call_id=f"read_{index}")
                 for index in range(4)]
        calls.insert(2, tool_message("execute_validation", {"checks": [{"type": "public_success_criteria"}]},
                                     call_id="check"))
        whole = ScriptedModel([*calls, final_message("failed")])
        run_invoke(builder, config={"keep_recent_reads": None}, ani=FakeANI({"get_state": state}), model=whole)
        size = max(len(message["content"]) for message in whole.seen_messages
                   if message.get("role") == "tool" and message["tool_call_id"].startswith("read_"))
        budget = 2 * size + size // 2
        model = ScriptedModel([*calls, final_message("failed")])
        report = run_invoke(builder, config={"keep_recent_reads": None, "keep_recent_read_chars": budget},
                            ani=FakeANI({"get_state": state}), model=model)

        answered = {message["tool_call_id"]: json.loads(message["content"])
                    for message in model.seen_messages if message.get("role") == "tool"}
        for index in (0, 1):
            stub = answered[f"read_{index}"]
            assert stub["elided"] is True and stub["operation"] == "get_state", name
            assert f"up to {budget} characters together" in stub["note"], name
        assert answered["read_2"] == state and answered["read_3"] == state, name
        assert "elided" not in answered["check"], "a validation is never touched"
        context = report["execution"]["context"]
        assert context["elided_results"] == 2, name
        assert (context["keep_recent_reads"], context["keep_recent_read_chars"]) == (None, budget), name
        assert report["execution"]["model_parameters"]["keep_recent_read_chars"] == budget, name


def test_the_character_budget_walks_back_from_the_last_answer_and_never_restores_a_stub():
    from sut.common.self_execute import _elide_older_reads

    def answer(call_id: str, size: int) -> list[dict[str, Any]]:
        return [{"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": "get_state",
                                                                                   "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": call_id, "content": "x" * size}]

    def stubbed(messages: list[dict[str, Any]]) -> list[bool]:
        return ['"elided": true' in message["content"] for message in messages if message["role"] == "tool"]

    messages = [{"role": "system", "content": "s"}]
    for index in range(4):
        messages += answer(f"c{index}", 100)
    elided: set[int] = set()
    assert _elide_older_reads(messages, elided, None, keep_chars=250) == (2, 200)
    assert stubbed(messages) == [True, True, False, False]
    # A newer, smaller answer leaves room the older stubs do not get back.
    messages += answer("c4", 10)
    assert _elide_older_reads(messages, elided, None, keep_chars=250) == (0, 0)
    assert stubbed(messages) == [True, True, False, False, False]
    # The answer to the last turn stays whole above the budget; the next turn stubs it.
    large = [{"role": "system", "content": "s"}, *answer("c0", 100), *answer("c1", 500)]
    assert _elide_older_reads(large, set(), None, keep_chars=250) == (1, 100)
    assert stubbed(large) == [True, False]
    large += [{"role": "assistant", "content": "thinking"}]
    assert _elide_older_reads(large, {2}, None, keep_chars=250) == (1, 500)
    # With both rules a result stays whole only if each keeps it.
    small = [{"role": "system", "content": "s"}]
    for index in range(5):
        small += answer(f"c{index}", 10)
    assert _elide_older_reads(small, set(), 2, keep_chars=1000) == (3, 30)


def test_a_budget_below_the_prompt_ends_the_episode_without_calling_the_provider():
    for name, builder in AGENTS:
        model = ScriptedModel([tool_message("get_state"), final_message("failed")])
        report = run_invoke(builder, config={"context_budget_chars": 500},
                            ani=FakeANI({"get_state": BIG_STATE}), model=model)

        assert model.call_count == 0, name
        assert report["status"] == "failed", name
        termination = report["execution"]["termination"]
        assert termination["cause"] == "context_budget" and termination["turn"] == 0, name
        assert "exceeds the context budget of 500" in report["error"], name
        assert report["execution"]["context"]["events"][0]["kind"] == "elided_reads", name
        assert report["execution"]["context"]["events"][0]["count"] == 0, name


def test_the_rejection_cap_ends_the_episode_by_name():
    for name, builder in AGENTS:
        model = ScriptedModel([final_message("completed")])
        report = run_invoke(builder, config={"max_consecutive_rejections": 3, "max_execution_seconds": 30.0},
                            ani=FakeANI({}), model=model)

        # Three claims, then the one tool-less diagnosis turn the loop asks after an
        # episode that ended without a conclusion (the claims stated no diagnosis).
        assert model.call_count == 4, name
        assert report["diagnosis"]["requested"] is True and report["diagnosis"]["text"] is None, name
        assert report["status"] == "failed", name
        assert report["execution"]["termination"] == {
            "cause": "completion_rejected",
            "detail": "completion claimed 3 times in a row without a passing public validation",
            "turn": 3,
        }, name
        assert [turn["kind"] for turn in report["execution"]["model_turns"]] == ["rejected_final"] * 3, name


def test_elision_never_touches_writes_or_validations():
    from sut.common import self_execute

    def call(name: str, call_id: str) -> dict[str, Any]:
        return {"id": call_id, "type": "function",
                "function": {"name": name, "arguments": json.dumps({"same": True})}}

    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": None, "tool_calls": [call("get_state", "r1"), call("update_config", "w1")]},
        {"role": "tool", "tool_call_id": "r1", "content": "first read"},
        {"role": "tool", "tool_call_id": "w1", "content": "first write"},
        {"role": "assistant", "content": None, "tool_calls": [call("execute_validation", "v1")]},
        {"role": "tool", "tool_call_id": "v1", "content": "first validation"},
        {"role": "assistant", "content": None, "tool_calls": [call("get_state", "r2"), call("update_config", "w2"),
                                                              call("execute_validation", "v2")]},
        {"role": "tool", "tool_call_id": "r2", "content": "second read"},
        {"role": "tool", "tool_call_id": "w2", "content": "second write"},
        {"role": "tool", "tool_call_id": "v2", "content": "second validation"},
    ]
    elided: set[int] = set()
    assert self_execute._elide_superseded_reads(messages, elided) == 1
    assert elided == {2}
    assert json.loads(messages[2]["content"])["superseded_by_call"] == "r2"
    assert messages[2]["role"] == "tool" and messages[2]["tool_call_id"] == "r1"
    assert [m["content"] for m in messages[3:]] == [
        "first write", None, "first validation", None, "second read", "second write", "second validation"]
    # A second pass finds nothing new.
    assert self_execute._elide_superseded_reads(messages, elided) == 0


def test_a_failed_re_read_does_not_elide_the_older_good_answer():
    """The newest identical read failed: the older result is the only answer the
    model has, so it stays whole; once a later read succeeds, elision resumes."""
    from sut.common.self_execute import _elide_superseded_reads

    def call(cid):
        return {"id": cid, "type": "function",
                "function": {"name": "get_state", "arguments": json.dumps({"scope": "all"})}}

    good = json.dumps({"ok": True, "state": "x" * 200})
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [call("r1")]},
        {"role": "tool", "tool_call_id": "r1", "content": good},
        {"role": "assistant", "content": None, "tool_calls": [call("r2")]},
        {"role": "tool", "tool_call_id": "r2", "content": json.dumps({"ok": False, "error": "timeout"})},
    ]
    elided: set = set()
    assert _elide_superseded_reads(messages, elided) == 0
    assert messages[1]["content"] == good and elided == set()
    messages += [
        {"role": "assistant", "content": None, "tool_calls": [call("r3")]},
        {"role": "tool", "tool_call_id": "r3", "content": good},
    ]
    assert _elide_superseded_reads(messages, elided) == 2
    assert json.loads(messages[1]["content"])["superseded_by_call"] == "r3"
    assert json.loads(messages[3]["content"])["superseded_by_call"] == "r3"


class RecordingModel(ScriptedModel):
    """A scripted model that keeps every transcript it was sent, not only the last."""

    def __init__(self, messages: list[dict[str, Any]]):
        super().__init__(messages)
        self.transcripts: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]], remaining_seconds: float) -> Any:
        self.transcripts.append([dict(message) for message in messages])
        return super().__call__(messages, remaining_seconds)


def test_past_turns_go_back_with_their_reasoning_and_only_the_recent_ones():
    """A reply's reasoning goes back under `reasoning`, the key vLLM hands to the chat
    template, on the last REASONING_TURNS_SENT_BACK assistant turns. Without it every past
    turn rendered as an empty think block and the model stopped reasoning after the first
    reply (E12 to E16). The plain-text turn and a turn with no reasoning are covered too."""
    from sut.common.messages import REASONING_KEY, REASONING_TURNS_SENT_BACK

    assert (REASONING_KEY, REASONING_TURNS_SENT_BACK) == ("reasoning", 3)
    replies = [
        {**tool_message("get_state", call_id="r1"), "reasoning_content": "think 1"},
        {"content": "I will read the state.", "tool_calls": None, "reasoning_content": "think 2"},
        tool_message("get_state", call_id="r3"),
        {**tool_message("get_state", call_id="r4"), "reasoning_content": "think 4"},
        {**tool_message("get_state", call_id="r5"), "reasoning_content": "think 5"},
        {**final_message("failed"), "reasoning_content": "think 6"},
    ]
    for name, builder in AGENTS:
        model = RecordingModel(replies)
        report = run_invoke(builder, config={}, ani=FakeANI({"get_state": GET_STATE_RESULT}), model=model)

        assert report["execution"]["termination"]["cause"] == "own_conclusion", name
        assert len(model.transcripts) == 6, name
        sent = [[message.get(REASONING_KEY) for message in call if message.get("role") == "assistant"]
                for call in model.transcripts]
        assert sent == [
            [],
            ["think 1"],
            ["think 1", "think 2"],
            ["think 1", "think 2", None],
            ["think 1", "think 2", None, "think 4"],
            [None, "think 2", None, "think 4", "think 5"],
        ], name
        assert not any("reasoning_content" in message for call in model.transcripts for message in call), \
            f"{name}: vLLM ignores reasoning_content on a request message"
        assert [turn["reasoning_chars"] for turn in report["execution"]["model_turns"]] == [7, 7, 0, 7, 7, 7], name
        context = report["execution"]["context"]
        assert context["reasoning_turns_sent_back"] == 3 and context["reasoning_dropped"] == 1, name


def test_the_task_names_the_tools_the_model_is_handed():
    """`tools` names the subject's surface, not the platform's whole list: a subject's own
    tools are tools all the same (2026-10-28)."""
    from benchmarks.platforms.containerlab import ANI_OPERATIONS

    for name, builder in AGENTS:
        model = ScriptedModel([final_message("failed")])
        surface = {"update_config": UPDATE_CONFIG_RESULT, "search_documents": {"ok": True}, "submit_result": {"ok": True}}
        run_invoke(builder, config={}, ani=FakeANI(surface), model=model,
                   task={**TASK, "tools": list(ANI_OPERATIONS)})
        task = json.loads(next(m["content"] for m in model.seen_messages if m.get("role") == "user"))
        assert task["tools"] == ["search_documents", "submit_result", "update_config"], name
        # A platform operation the judge did not declare stays out, whatever the surface.
        model = ScriptedModel([final_message("failed")])
        run_invoke(builder, config={}, ani=FakeANI(surface), model=model,
                   task={**TASK, "tools": ["submit_result"]})
        task = json.loads(next(m["content"] for m in model.seen_messages if m.get("role") == "user"))
        assert task["tools"] == ["search_documents", "submit_result"], name


def test_the_model_reads_the_task_without_the_rate_the_ani_holds_the_flow_to(monkeypatch):
    """The judge hands the subject the rate the protected flow measured on the healthy network
    (`healthy_throughput_mbps`), for its ANI's throughput check. In main04 the model, which read it
    under `observation`, took it for the rate under the fault in turn 1 of all 8 QoS episodes; it now
    reads the task without it, and the ANI still gets the whole task."""
    service = {"source": "host-access-1", "destination": "srv-site", "destination_ip": "10.1.0.10",
               "healthy_throughput_mbps": 7.651}
    task = {**TASK, "observation": {"service_measurement": service}}

    class RecordingANI(FakeANI):
        handed: list[tuple[str, Any]]

        def dispatch(self, operation, arguments, *, task=None):
            self.handed.append((operation, task))
            return super().dispatch(operation, arguments, task=task)

    for name, builder in AGENTS:
        ani = RecordingANI({"execute_validation": {"ok": False, "operation": "execute_validation", "checks": []}})
        ani.handed = []
        model = ScriptedModel([tool_message("execute_validation", {"checks": [{"type": "public_success_criteria"}]}),
                               final_message("failed")])
        run_invoke(builder, config={}, ani=ani, model=model, task=task)
        shown = next(m["content"] for m in model.seen_messages if m.get("role") == "user")
        assert "10.1.0.10" in shown and "healthy_throughput_mbps" not in shown, name
        handed = [given for operation, given in ani.handed if operation == "execute_validation"]
        assert handed and handed[0]["observation"]["service_measurement"]["healthy_throughput_mbps"] == 7.651, name


def test_a_subject_may_add_to_the_transcript_before_each_model_call():
    """`before_model_call`, when the ANI the loop holds has one:
    what it appends reaches the model at that call, and the rows it returns are filed with
    their source, inside the turn before them, and counted as no call of the model's."""

    class Hooked(FakeANI):
        def before_model_call(self, messages, *, task=None):
            self.hooked = getattr(self, "hooked", 0) + 1
            messages.append({"role": "user", "content": f"context {self.hooked}"})
            return [{"operation": "get_topology", "category": "read", "ok": True,
                     "duration_seconds": 0.0, "transaction_id": None, "error": None, "source": "own"}]

    for name, builder in AGENTS:
        ani = Hooked({"execute_validation": validation_result(False)})
        model = ScriptedModel([tool_message("execute_validation", {"checks": [{"type": "public_success_criteria"}]}),
                               final_message("failed")])
        report = run_invoke(builder, config={}, ani=ani, model=model)
        users = [m["content"] for m in model.seen_messages if m.get("role") == "user"]
        assert users[1:] == ["context 1", "context 2"], name
        rows = [(op["operation"], op.get("source")) for op in report["ani_operations"]]
        assert rows == [("get_topology", "own"), ("execute_validation", None),
                        ("get_topology", "own")], name
        assert [turn["operations"] for turn in report["execution"]["model_turns"]] == [[2, 3], []], name
        assert report["execution"]["tool_call_count"] == 1, name


def test_a_subject_without_the_hook_gets_the_transcript_it_always_had():
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(False)})
        model = ScriptedModel([final_message("failed")])
        run_invoke(builder, config={}, ani=ani, model=model)
        assert [m["role"] for m in model.seen_messages] == ["system", "user"], name
