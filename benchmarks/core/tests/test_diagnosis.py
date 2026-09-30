"""The judge's half of evaluation parameter 13.

Three things are pinned: every fault the catalogue can compile is put into words
that name its device; the ParaPLUIE score is read exactly from the answer tokens'
own log-probabilities and falls back, saying so, to the top log-probabilities of a
generated token; and the block the record carries says which question was answered
and which was not, never a zero for an unasked one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from benchmarks.core import ScenarioDefinition
from benchmarks.core import diagnosis as D

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- the injected fault, in words ---------------------------------------------------

def _compiled_methods() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    from scenarios.compiler.compiler import ScenarioCompiler

    found: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for topology in sorted((REPO_ROOT / "scenarios" / "topologies").glob("*.yaml")):
        compiler = ScenarioCompiler.from_topology_file(topology, seed=9)
        compiled = compiler.compile_scenarios(REPO_ROOT / "scenarios", expand_methods=True)
        for task in compiled.get("tasks", []):
            method = (task["private"].get("evaluator") or {}).get("selected_method")
            if method and method.get("name") not in found:
                found[method["name"]] = (task["id"], method, task["private"]["bindings"])
    return list(found.values())


def test_every_compiled_method_is_described_by_name_and_device():
    methods = _compiled_methods()
    assert len(methods) >= 30, "the catalogue shrank; the table below is checked against it"
    for scenario_id, method, bindings in methods:
        text = D.describe_fault(method, bindings)
        target = str(bindings["target"])
        assert target in text, (scenario_id, text)
        assert not text.startswith(f"On {target}, fault "), (scenario_id, "fell to the generic phrasing", text)
        assert text.endswith("."), (scenario_id, text)
        assert "None" not in text, (scenario_id, "an unbound value leaked", text)


def test_the_wording_carries_the_healthy_and_faulty_values():
    text = D.describe_fault(
        {"name": "endpoint_wrong_prefix_length",
         "operation": {"name": "set_interface_ipv4_address", "value_source": "wrong_prefix", "prefix_length": 32}},
        {"target": "app1", "interface": "eth1", "segment": "server_vlan",
         "healthy_ipv4": "10.10.50.10/24", "fault_ipv4": "10.10.50.10/32"})
    assert text == ("On app1, interface eth1 is configured with 10.10.50.10/32: the prefix "
                    "length is wrong, it should be 10.10.50.10/24.")
    text = D.describe_fault(
        {"name": "router_interface_address_removed",
         "operation": {"name": "set_interface_ipv4_presence", "present": False}},
        {"target": "leaf2", "interface": "ethernet-1/50", "segment": "server_vlan",
         "healthy_ipv4": "10.10.50.1/24"})
    assert "leaf2" in text and "ethernet-1/50" in text and "10.10.50.1/24" in text and "removed" in text


def test_an_operation_the_table_does_not_know_is_still_described():
    text = D.describe_fault({"name": "novel_fault", "operation": {"name": "set_something", "level": 3}},
                            {"target": "spine1", "interface": "e1", "affected_nodes": ["x"]})
    assert text == "On spine1, fault novel_fault: operation set_something (level=3); interface=e1."


def test_the_reference_comes_from_the_scenario_method():
    scenario = _scenario(method={"name": "router_ipv4_forwarding_disabled",
                                 "operation": {"name": "set_ipv4_forwarding", "enabled": False}},
                         bindings={"target": "leaf1", "affected_nodes": ["user1"]})
    reference = D.reference_for_scenario(scenario)
    assert reference == D.FaultReference(
        text="On leaf1, IPv4 forwarding (routing between its interfaces) is disabled.",
        method="router_ipv4_forwarding_disabled", operation="set_ipv4_forwarding", target="leaf1")
    assert D.reference_for_scenario(_scenario(method=None)) is None


def _scenario(*, method, bindings=None) -> ScenarioDefinition:
    return ScenarioDefinition.from_mapping({
        "id": "connectivity.x.m1", "version": "1.0.0", "domain": "connectivity", "intent": "fix",
        "topology": "t.yaml", "reference_state": "r.json", "fault": {}, "bindings": bindings or {},
        "oracles": {phase: {"path": phase, "version": "1.0.0"}
                    for phase in ("healthy", "expected_degradation", "repair", "preservation")},
        "method": method,
    })


# --- the subject's statement -------------------------------------------------------

def test_the_hypothesis_is_read_from_the_block_then_the_conclusion_then_the_summary():
    assert D.hypothesis_of({"diagnosis": {"text": "a", "source": "termination_turn"},
                            "final_response": {"diagnosis": "b", "summary": "c"}}) == ("a", "termination_turn")
    assert D.hypothesis_of({"diagnosis": {"text": None, "source": "final_response"},
                            "final_response": {"diagnosis": "b", "summary": "c"}}) == ("b", "final_response")
    assert D.hypothesis_of({"final_response": {"status": "failed", "summary": "c"}}) == ("c", "summary")
    assert D.hypothesis_of({"final_response": None}) == (None, None)
    assert D.hypothesis_of({}) == (None, None)


# --- the judge ---------------------------------------------------------------------

def _vllm(log_yes: float, log_no: float, *, exact: bool = True, top: list[tuple[str, float]] | None = None):
    """A stand-in for vLLM's chat endpoint: the prefix request answers with one token
    and its top log-probabilities; a continued answer reports the prompt token count
    grown by the answer's one token and that token's own log-probability."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path.endswith("/chat/completions")
        if body.get("continue_final_message"):
            if not exact:
                return httpx.Response(400, json={"error": "unknown field continue_final_message"})
            answer = body["messages"][-1]["content"]
            logprob = log_yes if answer == "Yes" else log_no
            return httpx.Response(200, json={
                "usage": {"prompt_tokens": 101},
                "prompt_logprobs": [None] + [{"1": {"logprob": -1.0, "decoded_token": "x"}}] * 99
                                   + [{"7": {"logprob": logprob, "decoded_token": answer}}],
                "choices": [{"message": {"content": ""}}],
            })
        listed = top if top is not None else [("Yes", log_yes), ("No", log_no), ("Maybe", -9.0)]
        return httpx.Response(200, json={
            "usage": {"prompt_tokens": 100},
            "choices": [{"message": {"content": listed[0][0]}, "logprobs": {"content": [{
                "token": listed[0][0], "logprob": listed[0][1],
                "top_logprobs": [{"token": token, "logprob": value} for token, value in listed],
            }]}}],
        })

    return httpx.MockTransport(handler), seen


def _judge(transport):
    return D.PLUIEJudge(D.JudgeConfig(model="judge", api_base="http://judge/v1"), transport=transport)


def test_the_exact_route_reads_the_answer_tokens_own_logprobs():
    transport, seen = _vllm(-0.05, -3.2)
    scored = _judge(transport).score("A", "B")
    assert scored["method"] == "prompt_logprobs" and "approximate" not in scored
    assert scored["log_p_yes"] == -0.05 and scored["log_p_no"] == -3.2
    assert scored["score"] == pytest.approx(3.15)
    assert scored["answer_tokens"] == {"Yes": ["Yes"], "No": ["No"]}
    # One prefix request for the token count, then the two continued answers.
    assert [body.get("continue_final_message", False) for body in seen] == [False, True, True]
    assert "logprobs" not in seen[0] and "top_logprobs" not in seen[0]
    assert {body["model"] for body in seen} == {"judge"}
    assert seen[1]["messages"][-1] == {"role": "assistant", "content": "Yes"}
    assert seen[1]["add_generation_prompt"] is False and seen[1]["prompt_logprobs"] == 0
    # The conversation is the *-PLUIE one: question, examples, data.
    assert seen[0]["messages"][0]["content"].startswith("You will receive two descriptions")
    assert seen[0]["messages"][-1] == {"role": "user", "content": 'A: "A"; B: "B"'}


def test_an_endpoint_without_prompt_logprobs_is_not_scored_and_the_block_says_why():
    """The papers read the two log-probabilities exactly; there is no approximation
    route, so an endpoint that cannot give them leaves the episode unjudged."""
    transport, _ = _vllm(-0.2, -2.0, exact=False)
    with pytest.raises(D.JudgeError) as refused:
        _judge(transport).score("A", "B")
    assert "per-token log-probabilities" in str(refused.value)
    block = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x", "source": "final_response"}},
                               _judge(transport))
    assert block["found"] is None and block["score"] is None
    assert block["reason"].startswith("judge error: JudgeError: the endpoint does not return")


def _openai(candidates, *, first=None, status=200):
    """A stand-in for the OpenAI chat API: one generated token with its candidates."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "logprobs refused"}})
        token, logprob = first if first else candidates[0]
        return httpx.Response(200, json={
            "usage": {"prompt_tokens": 100, "completion_tokens": 1},
            "choices": [{"message": {"content": token}, "logprobs": {"content": [{
                "token": token, "logprob": logprob,
                "top_logprobs": [{"token": t, "logprob": v} for t, v in candidates],
            }]}}],
        })

    return httpx.MockTransport(handler), seen


def _top_judge(transport, **extra):
    return D.PLUIEJudge(D.JudgeConfig(model="gpt-4.1", api_base="http://api/v1", reading="top_logprobs", **extra),
                        transport=transport)


def test_the_top_logprobs_reading_takes_both_answers_from_the_candidates():
    transport, seen = _openai([("Yes", -0.05), ("No", -3.2), ("Maybe", -9.0)])
    scored = _top_judge(transport).score("A", "B")
    assert scored["method"] == "top_logprobs" and scored["bound"] is False
    assert scored["score"] == pytest.approx(3.15)
    assert scored["answer_tokens"] == {"Yes": ["Yes"], "No": ["No"]}
    assert scored["raw"]["listed"] == 3 and scored["raw"]["first_token"] == "Yes"
    # One generated token, the candidates asked for, no continued turn.
    assert len(seen) == 1
    assert seen[0]["logprobs"] is True and seen[0]["top_logprobs"] == 20
    assert seen[0]["max_tokens"] == 1 and "continue_final_message" not in seen[0]
    assert seen[0]["messages"][-1] == {"role": "user", "content": 'A: "A"; B: "B"'}


def test_a_spelling_variant_counts_for_its_answer_at_its_best_form():
    transport, _ = _openai([("yes", -0.4), ("Yes", -0.1), (" No", -2.0)])
    scored = _top_judge(transport).score("A", "B")
    assert scored["answer_tokens"] == {"Yes": ["Yes"], "No": [" No"]}
    assert scored["score"] == pytest.approx(1.9)


def test_an_answer_missing_from_the_candidates_makes_the_score_a_bound():
    # Yes at 0.999 and one other candidate: No is below both the smallest listed
    # candidate and the mass left over, and the record says the score is a bound.
    transport, _ = _openai([("Yes", -0.001), ("Sure", -7.0)])
    scored = _top_judge(transport).score("A", "B")
    assert scored["bound"] is True and scored["answer_tokens"]["No"] == []
    assert scored["log_p_no"] <= -7.0 and scored["score"] > 0
    block = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x", "source": "final_response"}},
                               _top_judge(transport))
    assert block["found"] is True and block["judge"]["bound"] is True
    assert block["judge"]["reading"] == "top_logprobs" and block["judge"]["method"] == "top_logprobs"


def test_neither_answer_listed_is_not_judged():
    transport, _ = _openai([("**", -0.2), ("Sure", -2.0)])
    with pytest.raises(D.JudgeError) as refused:
        _top_judge(transport).score("A", "B")
    assert "neither Yes nor No" in str(refused.value)


def test_an_endpoint_refusing_logprobs_leaves_the_episode_unjudged():
    transport, _ = _openai([("Yes", -0.1)], status=403)
    block = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x"}}, _top_judge(transport))
    assert block["found"] is None and block["reason"].startswith("judge error")


def test_every_judge_call_is_appended_to_the_audit_log(tmp_path):
    log = tmp_path / "audit" / "diagnosis-audit.jsonl"
    transport, _ = _openai([("Yes", -0.05), ("No", -3.2)])
    judge = _top_judge(transport, audit_log=str(log))
    judge.score("fault A", "statement B")
    judge.score("fault A", "statement C")
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["reference"] == "fault A" and lines[1]["hypothesis"] == "statement C"
    assert lines[0]["judge"]["reading"] == "top_logprobs" and lines[0]["judge"]["prompt_id"] == D.PROMPT_ID
    assert lines[0]["raw"]["candidates"][0] == {"token": "Yes", "logprob": -0.05}
    assert lines[0]["score"] == pytest.approx(3.15) and lines[0]["bound"] is False
    block = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x"}}, judge)
    assert block["judge"]["audit_log"] == str(log)


def test_the_top_logprobs_request_asks_one_token_and_carries_the_extra_body():
    transport, seen = _openai([("Yes", -0.05), ("No", -3.2)])
    extra = {"provider": {"order": ["Novita"], "allow_fallbacks": False, "require_parameters": True},
             "reasoning": {"enabled": False}}
    _top_judge(transport, extra_body=extra).score("A", "B")
    assert seen[0]["max_tokens"] == 1 and "max_completion_tokens" not in seen[0]
    assert seen[0]["provider"] == extra["provider"] and seen[0]["reasoning"] == {"enabled": False}


def test_an_endpoint_that_wants_max_completion_tokens_gets_it_on_the_retry():
    calls = []

    def handler(request):
        body = json.loads(request.content); calls.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."}})
        return httpx.Response(200, json={"usage": {"prompt_tokens": 1}, "choices": [{"message": {"content": "Yes"}, "logprobs": {"content": [{
            "token": "Yes", "logprob": -0.1, "top_logprobs": [{"token": "Yes", "logprob": -0.1}, {"token": "No", "logprob": -2.4}]}]}}]})

    scored = _top_judge(httpx.MockTransport(handler)).score("A", "B")
    assert scored["score"] == pytest.approx(2.3)
    assert [("max_tokens" in c, "max_completion_tokens" in c) for c in calls] == [(True, False), (False, True)]


def test_the_reading_is_part_of_the_config_and_an_unknown_one_is_refused():
    judge = D.judge_from_config({"model": "gpt-4.1", "api_base": "https://api.openai.com/v1/",
                                 "api_key": "k", "reading": "top_logprobs", "audit_log": "/tmp/x.jsonl"})
    assert judge.config.reading == "top_logprobs" and judge.identity()["reading"] == "top_logprobs"
    assert D.judge_from_config({"model": "m", "api_base": "http://h/v1"}).config.reading == "prompt_logprobs"
    with pytest.raises(ValueError):
        D.judge_from_config({"model": "m", "api_base": "http://h/v1", "reading": "guess"})


def test_quotes_in_the_texts_cannot_close_the_template():
    messages = D.prompt_messages('he said "no"', "x\ny")
    assert messages[-1]["content"] == "A: \"he said 'no'\"; B: \"x y\""


def test_the_prompt_identity_is_stable_across_data():
    assert D.prompt_sha256() == D.prompt_sha256()
    assert D.PROMPT_ID == "fault-pluie-v2"


# --- the block -----------------------------------------------------------------------

class FakeJudge:
    def __init__(self, score: float | Exception):
        self.result = score
        self.calls: list[tuple[str, str]] = []

    def identity(self):
        return {"model": "fake", "api_base": "http://fake/v1", "prompt_id": D.PROMPT_ID}

    def score(self, reference, hypothesis):
        self.calls.append((reference, hypothesis))
        if isinstance(self.result, Exception):
            raise self.result
        return {"score": self.result, "log_p_yes": -0.1, "log_p_no": -0.1 - self.result,
                "method": "prompt_logprobs", "approximate": False}


REFERENCE = D.FaultReference(text="On leaf1, IPv4 forwarding is disabled.", method="m", operation="o", target="leaf1")


def test_a_judged_diagnosis_records_the_verdict_and_who_gave_it():
    judge = FakeJudge(2.5)
    block = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "leaf1 does not forward", "source": "final_response"}}, judge)
    assert block["found"] is True and block["score"] == 2.5
    assert block["hypothesis"] == "leaf1 does not forward" and block["hypothesis_source"] == "final_response"
    assert block["reference"] == REFERENCE.text and block["target"] == "leaf1"
    assert block["judge"]["model"] == "fake" and block["judge"]["method"] == "prompt_logprobs"
    assert block["reason"] is None and block["applicable"] is True
    assert judge.calls == [(REFERENCE.text, "leaf1 does not forward")]
    assert D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x"}}, FakeJudge(-0.4))["found"] is False


def test_the_questions_that_were_not_asked_are_not_zeros():
    no_judge = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x", "source": "final_response"}}, None)
    assert no_judge["found"] is None and no_judge["reason"] == "no diagnosis judge configured"
    assert no_judge["hypothesis"] == "x", "the statement is kept for scoring later"

    failed = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x"}}, FakeJudge(RuntimeError("down")))
    assert failed["found"] is None and failed["reason"].startswith("judge error: RuntimeError: down")
    assert failed["judge"]["model"] == "fake", "which judge failed is part of the record"

    no_fault = D.assess_diagnosis(REFERENCE, {"diagnosis": {"text": "x"}}, FakeJudge(1.0), fault_applicable=False)
    assert no_fault["found"] is None and no_fault["applicable"] is False

    no_method = D.assess_diagnosis(None, {"diagnosis": {"text": "x"}}, FakeJudge(1.0))
    assert no_method["found"] is None and "names no method" in no_method["reason"]


def test_a_subject_that_stated_nothing_did_not_find_the_problem():
    judge = FakeJudge(5.0)
    block = D.assess_diagnosis(REFERENCE, {"final_response": None, "diagnosis": {"text": None}}, judge)
    assert block["found"] is False and block["reason"] == "the subject stated no diagnosis"
    assert judge.calls == [], "nothing to compare, so the judge is not asked"
    assert D.assess_diagnosis(REFERENCE, {}, judge)["found"] is False


def test_the_judge_comes_from_the_experiment_config():
    assert D.judge_from_config(None) is None
    judge = D.judge_from_config({"model": "m", "api_base": "http://h/v1/", "api_key": "k"})
    assert judge.config == D.JudgeConfig(model="m", api_base="http://h/v1", api_key="k")
    assert judge.identity()["prompt_id"] == D.PROMPT_ID
