"""The intermediate step of evaluation parameter 13, on the subject's side.

The subject states the fault it identified: in its own final JSON when it reaches
one, and in one tool-less turn after the clock when it does not. What is pinned here
is the shape of that statement in the report, that the turn after the clock changes
nothing the repair was measured on, and that every shipped prompt asks for it.

Run: python -m pytest sut/common/tests/test_diagnosis_step.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_self_execute_loop import (  # noqa: E402
    AGENTS, TASK, FakeANI, GET_STATE_RESULT, ScriptedModel, run_invoke, tool_message,
    validation_result,
)

from sut.common.messages import (  # noqa: E402
    DIAGNOSIS_REQUEST, FINAL_STATUS_CONTRACT, parse_diagnosis, parse_final_response,
)
from sut.common.prompt_variants import DEFAULT_VARIANT, resolve_system_prompt  # noqa: E402

DIAGNOSIS = "leaf2's interface ethernet-1/50 lost its IPv4 address 10.10.50.1/24."


def final(status: str, **fields: Any) -> dict[str, Any]:
    return {"content": json.dumps({"status": status, "summary": "done", **fields}), "tool_calls": None}


class DiagnosisAwareModel(ScriptedModel):
    """Replays its script, and answers the diagnosis request with `answer` when the
    loop asks it after the clock (the request is the last user message then)."""

    def __init__(self, messages, *, answer: Any, **kwargs):
        super().__init__(messages, **kwargs)
        self.answer = answer
        self.diagnosis_prompts: list[list[dict[str, Any]]] = []

    def __call__(self, messages, remaining_seconds):
        if any(isinstance(m, dict) and m.get("role") == "user" and m.get("content") == DIAGNOSIS_REQUEST
               for m in messages):
            self.diagnosis_prompts.append([dict(m) for m in messages])
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        return super().__call__(messages, remaining_seconds)


# --- the parsers -----------------------------------------------------------------

def test_the_final_response_carries_the_diagnosis_and_says_when_it_is_missing():
    parsed = parse_final_response(json.dumps({"status": "failed", "summary": "s", "diagnosis": " x "}))
    assert parsed == {"status": "failed", "summary": "s", "diagnosis": "x"}
    assert parse_final_response(json.dumps({"status": "completed", "summary": "s"}))["diagnosis"] is None
    assert parse_final_response(json.dumps({"status": "completed", "diagnosis": ""}))["diagnosis"] is None


def test_the_diagnosis_turn_is_read_as_json_or_as_prose():
    assert parse_diagnosis('{"diagnosis": "leaf2 lost an address"}') == "leaf2 lost an address"
    assert parse_diagnosis("```json\n{\"diagnosis\": \"x\"}\n```") == "x"
    assert parse_diagnosis("The fault is on leaf2.") == "The fault is on leaf2."
    assert parse_diagnosis("   ") is None
    assert parse_diagnosis('{"diagnosis": ""}') is None


def test_the_shared_stanza_asks_for_a_diagnosis():
    assert '"diagnosis"' in FINAL_STATUS_CONTRACT
    assert "Do not call a tool" in DIAGNOSIS_REQUEST


# --- the loop --------------------------------------------------------------------

def test_a_conclusion_with_a_diagnosis_is_reported_from_the_final_response():
    for name, builder in AGENTS:
        ani = FakeANI({"execute_validation": validation_result(True)})
        model = ScriptedModel([tool_message("execute_validation"), final("completed", diagnosis=DIAGNOSIS)])
        report = run_invoke(builder, config={}, ani=ani, model=model)

        assert report["status"] == "completed", name
        assert report["final_response"]["diagnosis"] == DIAGNOSIS, name
        assert report["diagnosis"] == {
            "text": DIAGNOSIS, "source": "final_response", "requested": False, "error": None,
        }, name
        assert model.call_count == 2, (name, "no diagnosis turn after an own conclusion")


def test_a_conclusion_without_a_diagnosis_is_reported_as_stating_none():
    for name, builder in AGENTS:
        ani = FakeANI({})
        model = ScriptedModel([final("failed")])
        report = run_invoke(builder, config={}, ani=ani, model=model)

        assert report["final_response"]["diagnosis"] is None, name
        assert report["diagnosis"]["text"] is None, name
        assert report["diagnosis"]["source"] == "final_response", name
        assert report["diagnosis"]["requested"] is False, name
        assert "no diagnosis" in report["diagnosis"]["error"], name


def test_an_episode_the_clock_ended_is_asked_once_without_tools():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = DiagnosisAwareModel(
            [tool_message("get_state")], delay=0.05,
            answer={"content": json.dumps({"diagnosis": DIAGNOSIS}), "tool_calls": None,
                    "_ibn_usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}},
        )
        report = run_invoke(builder, config={}, ani=ani, model=model,
                            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}})

        assert report["status"] == "timeout", name
        assert report["execution"]["termination"]["cause"] == "budget", name
        assert report["final_response"] is None, name
        diagnosis = report["diagnosis"]
        assert diagnosis["text"] == DIAGNOSIS, name
        assert diagnosis["source"] == "termination_turn", name
        assert diagnosis["requested"] is True and diagnosis["error"] is None, name
        assert diagnosis["calls"] == 1 and diagnosis["seconds"] >= 0, name
        # Asked with the transcript as it stood, plus the one question.
        assert len(model.diagnosis_prompts) == 1, name
        prompt = model.diagnosis_prompts[0]
        assert prompt[-1] == {"role": "user", "content": DIAGNOSIS_REQUEST}, name
        assert prompt[0]["role"] == "system", name
        # The turn is not one of the repair's, and its tokens are part of the cost.
        assert report["execution"]["termination"]["turn"] == len(report["execution"]["model_turns"]), name
        assert all(turn["kind"] != "diagnosis" for turn in report["execution"]["model_turns"]), name
        assert report["execution"]["token_usage"]["total_tokens"] == 10, name
        # And nothing the repair was measured on moved.
        assert report["verified"] is False and report["device_changes"] == [], name
        assert [call for call, _ in ani.calls if call != "get_state"] == [], name


def test_a_tool_call_in_the_diagnosis_turn_is_refused_once_then_recorded():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = DiagnosisAwareModel([tool_message("get_state")], delay=0.05,
                                    answer=tool_message("get_state"))
        calls_before = len(ani.calls)
        report = run_invoke(builder, config={}, ani=ani, model=model,
                            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}})

        diagnosis = report["diagnosis"]
        assert diagnosis["text"] is None, name
        assert diagnosis["calls"] == 2, (name, "one refusal, one repeat")
        assert "called a tool" in diagnosis["error"], name
        assert len(model.diagnosis_prompts) == 2, name
        assert model.diagnosis_prompts[1][-1]["content"].startswith("No tool will be dispatched"), name
        # The tool it asked for was never dispatched by the diagnosis turn.
        assert all(call == "get_state" for call, _ in ani.calls[calls_before:]), name
        assert report["status"] == "timeout", name


def test_a_provider_failure_in_the_diagnosis_turn_leaves_the_verdict_alone():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = DiagnosisAwareModel([tool_message("get_state")], delay=0.05,
                                    answer=RuntimeError("provider exploded"))
        report = run_invoke(builder, config={}, ani=ani, model=model,
                            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}})

        assert report["status"] == "timeout", name
        assert report["execution"]["termination"]["cause"] == "budget", name
        assert report["diagnosis"]["text"] is None, name
        assert report["diagnosis"]["error"] == "RuntimeError: provider exploded", name


def test_prose_in_the_diagnosis_turn_is_kept():
    for name, builder in AGENTS:
        ani = FakeANI({"get_state": GET_STATE_RESULT})
        model = DiagnosisAwareModel([tool_message("get_state")], delay=0.05,
                                    answer={"content": "leaf2 has no address on ethernet-1/50.", "tool_calls": None})
        report = run_invoke(builder, config={}, ani=ani, model=model,
                            task={**TASK, "execution_budget": {"wall_clock_seconds": 1}})
        assert report["diagnosis"]["text"] == "leaf2 has no address on ethernet-1/50.", name


# --- the prompts -----------------------------------------------------------------

def test_every_prompt_on_the_shared_stanza_asks_for_the_diagnosis():
    # The subjects whose prompt files take the shared stanza. A prompt that still
    # spells the old two-field JSON out (the RAG subject, by decision) states no
    # diagnosis and is scored from its summary instead.
    subjects = sorted({path.parent.parent for path in (REPO_ROOT / "sut").glob("*/prompts/*.txt")
                       if "{FINAL_STATUS_CONTRACT}" in path.read_text(encoding="utf-8")})
    if not subjects:
        pytest.skip("no shipped prompt asks for the shared stanza on this branch; "
                    "the judge then scores each subject's final summary")
    for subject in subjects:
        variants = [DEFAULT_VARIANT] + sorted(
            path.stem for path in (subject / "prompts").glob("*.txt")
            if path.stem not in {DEFAULT_VARIANT, "question"})
        for variant in variants:
            text = resolve_system_prompt(subject, variant)
            assert FINAL_STATUS_CONTRACT in text, (subject.name, variant)
            assert "{FINAL_STATUS_CONTRACT}" not in text, (subject.name, variant)
            assert "`diagnosis`" in text, (subject.name, variant)
