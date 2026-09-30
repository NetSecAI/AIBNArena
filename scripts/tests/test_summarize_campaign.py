"""The campaign summary must group by what a result records, not where it sits.

Each test writes result documents the way the judge writes them and reads the
table back. The smoke test at the end runs on the real 2026-09-05 campaign when
it is on this machine: those counts were checked by hand against the 59 files.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "summarize_campaign", REPO / "scripts" / "summarize_campaign.py")
summarize_campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summarize_campaign)

CAMPAIGN_20260905 = Path("/home/jongmin/ibn-integration-test/runs/20260905")

BASE_PARAMETERS: dict[str, Any] = {
    "model": "openai/qwen3-8b-think",
    "prompt_variant": "default",
    "enable_thinking": None,
    "thinking_mechanism": "unset",
    "thinking_request": {},
    "ani_call_limit": None,
    "max_tokens": 8000,
    "max_execution_seconds": 1800.0,
    "recursion_limit": 50,
    "temperature": None,
    "thinking_style": "auto",
}


def make_result(**overrides: Any) -> dict[str, Any]:
    """A completed episode that concluded on its own, shaped like a judge result."""
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": "connectivity-smoke-a-0123456789ab",
        "experiment_id": "connectivity-smoke-a",
        "status": "completed",
        "error": None,
        "scenario": {"id": "connectivity.remove_ip.m1", "domain": "connectivity", "version": "1.0.0"},
        "evaluations": {"repair": {"passed": False, "phase": "repair"}},
        "operation_counts": {"ani_mutations": 3, "successful_mutations": 1, "output_tokens": 1200},
        "provenance": {
            "sut_identity": "LangChain ANI Baseline",
            "configured_model": "openai/qwen3-8b-think",
            "sut_reported_model": "openai/qwen3-8b-think",
            "model_parameters": copy.deepcopy(BASE_PARAMETERS),
            "git": {"commit": "02d71e98454cb589d2126d7ad872066b9f7488f4", "dirty": False},
            "oracle_versions": {"healthy": "1.0.0", "repair": "1.0.0"},
        },
        "sut_result": {
            "status": "failed",
            "error": "The repair could not be completed.",
            "final_response": "The repair could not be completed.",
            "execution": {
                "elapsed_seconds": 300.0,
                "budget_seconds": 1800.0,
                "trace_ref": "traces/0123456789ab.jsonl",
                "token_usage": {"input_tokens": 50000, "output_tokens": 1200, "total_tokens": 51200},
                "model_turns": [
                    {"turn": 1, "kind": "tool_calls"},
                    {"turn": 2, "kind": "final"},
                ],
            },
        },
    }
    for dotted, value in overrides.items():
        node = document
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return document


def write_results(root: Path, documents: dict[str, Any]) -> None:
    for relative, document in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document) if isinstance(document, dict) else document,
                        encoding="utf-8")


def cause(document: dict[str, Any]) -> str:
    return summarize_campaign.termination_cause(document)


# --- termination cause ---------------------------------------------------------

def test_old_style_timeout_at_the_deadline_is_the_budget():
    document = make_result(**{
        "sut_result.error": "litellm.Timeout: APITimeoutError - Request timed out.",
        "sut_result.final_response": None,
        "sut_result.execution.elapsed_seconds": 1818.07,
        "sut_result.execution.model_turns": [{"turn": 1, "kind": "tool_calls"},
                                             {"turn": 2, "kind": "error"}],
    })
    assert cause(document) == "budget"


def test_old_style_timeout_well_inside_the_budget_is_a_model_error():
    document = make_result(**{
        "sut_result.error": "litellm.Timeout: APITimeoutError - Request timed out.",
        "sut_result.final_response": None,
        "sut_result.execution.elapsed_seconds": 300.0,
        "sut_result.execution.model_turns": [{"turn": 1, "kind": "error"}],
    })
    assert cause(document) == "model_error"


def test_old_style_context_window_error_wins_over_the_clock():
    document = make_result(**{
        "sut_result.error": "litellm.ContextWindowExceededError: litellm.BadRequestError: ...",
        "sut_result.final_response": None,
        "sut_result.execution.elapsed_seconds": 1790.0,
        "sut_result.execution.model_turns": [{"turn": 1, "kind": "error"}],
    })
    assert cause(document) == "context_window"


def test_subject_status_timeout_is_the_budget_whatever_the_error_says():
    document = make_result(**{
        "sut_result.status": "timeout",
        "sut_result.error": "SUT claimed completion without a passing public validation",
        "sut_result.final_response": None,
        "sut_result.execution.model_turns": [{"turn": 1, "kind": "rejected_final"}],
    })
    assert cause(document) == "budget"


def test_new_style_termination_field_is_read_before_any_fallback():
    document = make_result(**{
        "sut_result.error": "litellm.Timeout: APITimeoutError - Request timed out.",
        "sut_result.execution.elapsed_seconds": 1818.07,
        "sut_result.execution.termination": {"cause": "tool_budget", "detail": "ani limit"},
    })
    assert cause(document) == "tool_budget"


def test_judge_failure_without_execution_is_named_by_its_phase():
    document = make_result(**{
        "status": "failed",
        "error": {"type": "LifecycleError", "phase": "evaluate_degradation",
                  "message": "expected_degradation oracle failed"},
        "sut_result": None,
        "provenance.sut_identity": "unavailable",
        "provenance.model_parameters": {},
    })
    assert cause(document) == "judge_evaluate_degradation"


def test_own_conclusion_and_completed():
    assert cause(make_result()) == "own_conclusion"
    assert cause(make_result(**{"sut_result.status": "completed",
                                "sut_result.error": None})) == "completed"


def test_repeat_index_comes_only_from_the_experiment_id_suffix():
    assert summarize_campaign.repeat_index("connectivity-smoke-a-r3") == 3
    assert summarize_campaign.repeat_index("connectivity-smoke-a") is None
    assert summarize_campaign.repeat_index("r3-connectivity") is None
    assert summarize_campaign.repeat_index(None) is None


# --- cells ----------------------------------------------------------------------

def test_two_results_with_the_same_key_share_one_cell(tmp_path: Path):
    write_results(tmp_path, {
        "lane_A/connectivity-smoke_20260905T020005/results/a.json": make_result(
            **{"run_id": "run-a", "evaluations.repair.passed": True}),
        "later/other-name_20260906T000000/results/b.json": make_result(
            **{"run_id": "run-b", "experiment_id": "connectivity-smoke-a-r2"}),
    })
    summary = summarize_campaign.build_summary(tmp_path)
    assert summary["broken"] == []
    assert len(summary["cells"]) == 1
    cell = summary["cells"][0]
    assert cell["n"] == 2
    assert cell["repair_passed"] == 1
    assert cell["repair_rate"] == 0.5
    assert cell["condition"] == "default"
    assert cell["model"] == ["openai/qwen3-8b-think"]
    assert sorted(cell["runs"]) == ["run-a", "run-b"]
    assert cell["repeats"] == [2]
    assert cell["termination"] == {"own_conclusion": 2}
    assert cell["model_turns"] == {"median": 2, "min": 2, "max": 2, "n": 2}
    assert cell["ani_mutations"]["median"] == 3
    assert cell["successful_mutations"]["max"] == 1
    assert cell["flags"] == {}


def test_a_different_condition_is_a_different_cell(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/a.json": make_result(),
        "x/results/b.json": make_result(**{"provenance.model_parameters.prompt_variant": "terse"}),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert [c["condition"] for c in cells] == ["default", "p-terse"]
    assert all(c["n"] == 1 for c in cells)


def test_broken_file_is_listed_and_the_rest_is_summarised(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/good.json": make_result(),
        "x/results/truncated.json": '{"status": "completed", "sut_result": {',
        "x/results/list.json": "[1, 2, 3]",
    })
    summary = summarize_campaign.build_summary(tmp_path)
    assert summary["totals"]["results"] == 1
    assert summary["totals"]["broken"] == 2
    assert sorted(Path(b["path"]).name for b in summary["broken"]) == ["list.json", "truncated.json"]
    assert all(b["error"] for b in summary["broken"])
    text = summarize_campaign.render_text(summary)
    assert "broken files 2" in text
    assert "truncated.json" in text


def test_condition_label_depends_on_the_parameters_alone(tmp_path: Path):
    parameters = dict(BASE_PARAMETERS, enable_thinking=False, thinking_mechanism="chat_template",
                      thinking_request={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}})
    write_results(tmp_path, {
        "thinking/dhcp_dns-smoke_20260905T093533/results/dhcp_dns-smoke-b-1.json":
            make_result(**{"provenance.model_parameters": parameters}),
        "a/completely/different/place/results/zzz.json":
            make_result(**{"provenance.model_parameters": parameters, "run_id": "other"}),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(cells) == 1
    assert cells[0]["condition"] == "thinkoff"
    assert cells[0]["n"] == 2


@pytest.mark.parametrize("changes, label", [
    ({}, "default"),
    ({"prompt_variant": "terse"}, "p-terse"),
    ({"enable_thinking": False, "thinking_mechanism": "chat_template",
      "thinking_request": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}},
     "thinkoff"),
    ({"enable_thinking": True, "thinking_mechanism": "chat_template",
      "thinking_request": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}},
     "thinkon"),
    ({"enable_thinking": True, "thinking_mechanism": "reasoning_effort"}, "thinkon-reasoning_effort"),
    ({"ani_call_limit": 6}, "ani6"),
    ({"ani_call_limit": 6, "max_execution_seconds": 900.0}, "ani6+900s"),
    ({"max_tokens": 4000}, "tok4000"),
    ({"prompt_variant": "terse", "min_retry_tokens": 256, "tool_result_chars": 4000,
      "context_budget_chars": 120000}, "p-terse+retry256+trc4000+ctx120000"),
])
def test_condition_labels(changes: dict[str, Any], label: str):
    parameters = dict(BASE_PARAMETERS, **changes)
    assert summarize_campaign.condition_label(parameters) == label
    assert summarize_campaign.condition_label({}) == "unrecorded"


def test_a_missing_prompt_variant_joins_the_default_cell_and_is_flagged(tmp_path: Path):
    old = copy.deepcopy(BASE_PARAMETERS)
    del old["prompt_variant"]
    write_results(tmp_path, {
        "x/results/old.json": make_result(**{"provenance.model_parameters": old}),
        "x/results/new.json": make_result(**{"run_id": "new"}),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(cells) == 1
    assert cells[0]["n"] == 2
    assert cells[0]["flags"] == {"unrecorded_prompt_variant": 1}


def test_flags_name_what_makes_the_cell_incomparable(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{
            "run_id": "a",
            "provenance.git.dirty": True,
            "provenance.sut_reported_model": "openai/other",
            "sut_result.execution.trace_ref": None,
        }),
        "x/results/b.json": make_result(**{
            "run_id": "b",
            "provenance.git.commit": "2f54f9a0000000000000000000000000000000000",
            "provenance.oracle_versions": {"healthy": "1.0.0", "repair": "1.1.0"},
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(cells) == 1
    assert cells[0]["flags"] == {
        "git_dirty": 1,
        "mixed_commit": 2,
        "model_mismatch": 1,
        "no_trace_ref": 1,
        "mixed_oracle_versions": 2,
    }


def test_judge_failure_without_execution_is_not_a_missing_trace(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{
            "status": "failed",
            "error": {"type": "LifecycleError", "phase": "evaluate_degradation", "message": "m"},
            "sut_result": None,
            "provenance.sut_identity": "unavailable",
            "provenance.model_parameters": {},
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert cells[0]["condition"] == "unrecorded"
    assert cells[0]["termination"] == {"judge_evaluate_degradation": 1}
    assert cells[0]["elapsed_seconds"]["n"] == 0
    assert "no_trace_ref" not in cells[0]["flags"]


def test_by_collapses_the_fields_left_out(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/a.json": make_result(),
        "x/results/b.json": make_result(**{"scenario.id": "connectivity.remove_ip.m2", "run_id": "b"}),
        "x/results/c.json": make_result(**{"provenance.sut_identity": "Other ANI Subject",
                                           "run_id": "c"}),
    })
    cells = summarize_campaign.build_summary(tmp_path, by=("subject", "condition"))["cells"]
    assert [(c["subject"], c["n"]) for c in cells] == [
        ("LangChain ANI Baseline", 2), ("Other ANI Subject", 1)]
    assert cells[0]["scenario"] == ["connectivity.remove_ip.m1", "connectivity.remove_ip.m2"]
    text = summarize_campaign.render_text(
        summarize_campaign.build_summary(tmp_path, by=("subject", "condition")))
    assert "2 scenarios" in text


def test_cli_json_and_by(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    write_results(tmp_path, {"x/results/a.json": make_result()})
    assert summarize_campaign.main([str(tmp_path), "--json", "--by", "subject"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["by"] == ["subject"]
    assert summary["totals"] == {
        "results": 1, "broken": 0, "repair_passed": 0, "scored": 1,
        "judge_status": {"completed": 1}, "termination": {"own_conclusion": 1},
    }
    with pytest.raises(SystemExit):
        summarize_campaign.main([str(tmp_path), "--by", "lane"])


# --- the real campaign ---------------------------------------------------------

@pytest.mark.skipif(not CAMPAIGN_20260905.is_dir(), reason="campaign directory not on this machine")
def test_smoke_campaign_20260905():
    results, broken = summarize_campaign.load_results(CAMPAIGN_20260905)
    assert broken == []
    assert len(results) == 59
    histogram = Counter(r.termination for r in results)
    # 8 ContextWindowExceeded, 2 subject status "timeout" plus 11 provider Timeouts
    # with the clock at the budget, and 2 judge failures in evaluate_degradation.
    assert histogram["context_window"] == 8
    assert histogram["budget"] == 13
    assert histogram["judge_evaluate_degradation"] == 2
    assert histogram["own_conclusion"] == 36
    assert "model_error" not in histogram
    assert sum(1 for r in results if r.termination == "budget"
               and (r.document.get("sut_result") or {}).get("status") == "timeout") == 2
    summary = summarize_campaign.build_summary(CAMPAIGN_20260905)
    assert summary["totals"]["termination"] == dict(histogram)
    assert sum(c["n"] for c in summary["cells"]) == 59


def test_a_wrong_shaped_result_is_listed_and_does_not_abort(tmp_path: Path):
    """A file that parses but is not shaped like a result is broken, not fatal."""
    write_results(tmp_path, {
        "x/results/good.json": make_result(),
        "x/results/shape.json": json.dumps(
            {"scenario": "not-a-mapping", "sut_result": [], "provenance": 3}),
    })
    summary = summarize_campaign.build_summary(tmp_path)
    assert summary["totals"]["results"] == 1
    assert summary["totals"]["broken"] == 1
    assert Path(summary["broken"][0]["path"]).name == "shape.json"


def test_a_judge_not_told_a_model_is_not_a_mismatch(tmp_path: Path):
    # Results recorded before --configured-model existed carry null there; the
    # subject's own report alone cannot disagree with nothing.
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{
            "run_id": "a",
            "provenance.configured_model": None,
            "provenance.sut_reported_model": "openai/other",
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(cells) == 1
    assert "model_mismatch" not in cells[0]["flags"]


def test_the_rejection_cap_is_a_condition_of_its_own(tmp_path: Path):
    # Two draws that differ only in max_consecutive_rejections are two cells: the
    # launcher labels the cap (rej<N>) and every record carries it.
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{"run_id": "a"}),
        "x/results/b.json": make_result(**{
            "run_id": "b",
            "provenance.model_parameters.max_consecutive_rejections": 12,
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(cells) == 2


def test_the_clock_decides_the_budget_not_the_subject_s_wording():
    """Requiring "Timeout" in the error let a subject fall past every branch.

    sut/langchain_agent says "execution budget exceeded", carries no
    `execution.termination` and no `final_response`, so its wall-clock episodes
    classified as `unclassified` -- and an episode the clock ended was then counted
    as one it did not end.
    """
    document = make_result(**{
        "sut_result.status": "failed",
        "sut_result.error": "execution budget exceeded",
        "sut_result.final_response": None,
        "sut_result.execution.termination": None,
        "sut_result.execution.elapsed_seconds": 411.58,
        "sut_result.execution.budget_seconds": 400.0,
        "sut_result.execution.model_turns": [],
    })
    assert cause(document) == "budget"


def test_a_knob_recorded_as_null_keys_the_same_as_a_knob_not_recorded():
    """"Key absent" and "key present with value null" are the same condition.

    They produced different JSON, so a result written before a subject version added
    a knob keyed differently from one written after it, and one cell became two.
    """
    without = dict(BASE_PARAMETERS)
    without.pop("ani_call_limit")
    with_null = dict(BASE_PARAMETERS, ani_call_limit=None)

    assert summarize_campaign.condition_of(without).key == \
        summarize_campaign.condition_of(with_null).key

    turned = dict(BASE_PARAMETERS, ani_call_limit=40)
    assert summarize_campaign.condition_of(turned).key != \
        summarize_campaign.condition_of(with_null).key, \
        "a knob that was actually turned is still its own condition"


def test_an_episode_the_subject_never_saw_is_out_of_the_repair_denominator(tmp_path: Path):
    """A judge-side failure leaves `sut_result` empty; it is not a failed repair.

    Dividing by every member of the cell counted a testbed that never came up as an
    episode the agent failed to repair -- the same conflation the evaluation
    parameters take care to avoid, in the table beside them.
    """
    ran = make_result(**{"evaluations.repair.passed": True})
    abandoned = make_result(**{
        "status": "failed",
        "sut_result": {},
        "error": {"phase": "deploy_reset_testbed", "message": "the lab never came up"},
        "evaluations": {},
    })
    write_results(tmp_path, {"x/results/ran.json": ran,
                             "x/results/abandoned.json": abandoned})

    results, _ = summarize_campaign.load_results(tmp_path)
    cell = summarize_campaign.summarize_cell(results, ["subject"])
    assert cell["n"] == 2
    assert cell["scored"] == 1
    assert cell["repair_passed"] == 1
    assert cell["repair_rate"] == 1.0, "one episode ran, and it repaired"


# --- intent: the wording of the task is a cell field, not a condition ---------------

def test_by_intent_separates_the_wordings_and_the_default_cell_does_not(tmp_path: Path):
    """`provenance.intent_variant` is how the intent-precision runs are read.

    It is not a CONDITION_FIELD: the condition says how the subject was run, the
    intent says what it was asked. The default cell stays as it always was, so a
    directory that mixes old and new records does not split one scenario in two.
    """
    for name, intent in (("a", "low"), ("b", "medium"), ("c", "high")):
        write_results(tmp_path, {f"x/results/{name}.json": make_result(
            **{"run_id": f"run-{name}", "provenance.intent_variant": intent})})

    cells = summarize_campaign.build_summary(tmp_path, by=("subject", "intent"))["cells"]
    assert [(c["intent"], c["n"]) for c in cells] == [("high", 1), ("low", 1), ("medium", 1)]

    default = summarize_campaign.build_summary(tmp_path)["cells"]
    assert len(default) == 1 and default[0]["n"] == 3
    assert default[0]["intent"] == ["high", "low", "medium"]
    text = summarize_campaign.render_text(summarize_campaign.build_summary(tmp_path))
    assert "intent" in text.splitlines()[0]
    assert "3 intents" in text


def test_a_missing_wording_reads_unrecorded_and_a_null_one_unnamed(tmp_path: Path):
    """Key absent and key null are different facts and must not share a name."""
    write_results(tmp_path, {
        "x/results/absent.json": make_result(run_id="run-absent"),
        "x/results/null.json": make_result(**{"run_id": "run-null", "provenance.intent_variant": None}),
        "x/results/low.json": make_result(**{"run_id": "run-low", "provenance.intent_variant": "low"}),
    })
    cells = summarize_campaign.build_summary(tmp_path, by=("subject", "intent"))["cells"]
    assert [c["intent"] for c in cells] == ["low", "unnamed", "unrecorded"]


def test_the_cli_accepts_intent_as_a_cell_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    write_results(tmp_path, {"x/results/a.json": make_result(**{"provenance.intent_variant": "high"})})
    assert summarize_campaign.main([str(tmp_path), "--json", "--by", "subject,intent"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["by"] == ["subject", "intent"]
    assert summary["cells"][0]["intent"] == "high"


def test_a_langchain_shaped_record_reads_its_own_end():
    """The LangChain subject writes final_response as the loop does; no fallback guessing."""
    concluded = make_result(**{
        "sut_result.status": "failed",
        "sut_result.error": "no safe repair",
        "sut_result.final_response": {"status": "failed", "summary": "no safe repair"},
        "sut_result.execution.termination": {"cause": "own_conclusion", "detail": "failed", "turn": 4},
    })
    assert cause(concluded) == "own_conclusion"
    stopped = make_result(**{"sut_result.execution.termination": {
        "cause": "repeated_failed_requests",
        "detail": "stopped after 3 identical failed get_state requests", "turn": 4}})
    assert cause(stopped) == "repeated_failed_requests"


def test_a_provider_that_answered_with_another_model_is_flagged(tmp_path: Path):
    """The name that matters is the one that answered.

    `configured_model` and `sut_reported_model` are both the request: the judge's copy
    and the subject's. A gateway that aliases a model name instead of refusing it
    leaves those two agreeing and only the provider's differing, which is the case
    this flag used not to see.
    """
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{
            "run_id": "a",
            "provenance.provider_reported_model": "openai/something-else",
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert cells[0]["flags"].get("model_mismatch") == 1


def test_a_provider_that_reported_no_model_is_not_a_mismatch(tmp_path: Path):
    write_results(tmp_path, {
        "x/results/a.json": make_result(**{
            "run_id": "a",
            "provenance.provider_reported_model": None,
        }),
    })
    cells = summarize_campaign.build_summary(tmp_path)["cells"]
    assert "model_mismatch" not in cells[0]["flags"]
