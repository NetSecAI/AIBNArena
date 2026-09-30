"""What the evaluation parameters must not get wrong.

Each test here stands for one way the recorded episode misleads a naive reading. The
corpus numbers at the bottom are the same ones a hand count produced, and they are
pinned so a future definition change has to admit it moved them.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "evaluation_parameters.py"
CAMPAIGN = Path("/home/jongmin/ibn-integration-test/runs/20260906")


def _load():
    for path in (str(REPO), str(REPO / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("evaluation_parameters", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EP = _load()


def episode(operations=(), **overrides):
    """A judge-shaped result with the fields these parameters read."""
    document = {
        "status": "completed",
        "evaluations": {"repair": {"passed": False}},
        "sut_result": {
            "status": "failed",
            "ani_operations": list(operations),
            "execution": {"termination": {"cause": "own_conclusion"}},
        },
    }
    for dotted, value in overrides.items():
        target = document
        parts = dotted.split("__")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return document


def op(operation, **extra):
    return {"operation": operation, "arguments": extra.pop("arguments", {}),
            "ok": extra.pop("ok", True), "error": extra.pop("error", None), **extra}


def test_the_suts_own_reads_are_not_the_models_actions():
    """A row carrying `source` was issued by the subject's own tool, not chosen."""
    facts = EP.episode_facts(episode([
        op("get_running_config", source="subject_refresh"),
        op("get_running_config", source="subject_refresh"),
        op("get_topology"),
    ]))
    assert facts.actions == 1
    assert facts.repeats == 0, "internal reads must not inflate the repeat rate"
    assert facts.buckets["check_config"] == 1, "only the model's own read is counted"


def test_a_validation_that_honestly_failed_is_an_accepted_call():
    """`ok` on a validation is the verdict, not the health of the call."""
    facts = EP.episode_facts(episode([
        op("execute_validation", ok=False, error=None),
        op("execute_validation", ok=False, error="icmp.destination_ip is required"),
    ]))
    assert facts.actions == 2
    assert facts.accepted == 1


def test_a_push_the_device_rejected_is_not_an_accepted_call():
    """`update_config` reports a refusal through `ok`, with `error` left empty.

    The opposite of the validation case: there `ok` is a verdict and `error` says
    whether the call was healthy, here `error` can be empty on a push the device
    turned down. Reading only `error` counts that as the surface accepting it.
    """
    facts = EP.episode_facts(episode([
        op("update_config", ok=False, error=None),
        op("update_config", ok=True, error=None),
    ]))
    assert facts.actions == 2
    assert facts.accepted == 1


def test_the_device_a_change_reached_is_read_from_the_key_the_record_uses():
    """An applied action names its device in `machine`; there is no `target` there."""
    document = episode()
    document["sut_result"]["device_changes"] = [
        {"action": {"machine": "leaf2", "command": "set / x", "reason": "r"},
         "operation": "update_config"}]
    assert EP.edited_targets(document["sut_result"]) == {"leaf2"}


def test_a_submission_rate_stays_within_its_own_denominator():
    """A numerator over every episode against a denominator over submissions.

    An episode the clock ended never submitted, so it is out of the denominator --
    and it satisfied the numerator whenever the oracle judged it, which made a rate
    that could exceed one.
    """
    clock_ended = episode()
    clock_ended["evaluations"] = {"repair": {"passed": True}}
    clock_ended["sut_result"]["execution"] = {"termination": {"cause": "budget"}}
    submission = episode()

    parameters = EP.parameters_for([
        EP.episode_facts(clock_ended), EP.episode_facts(clock_ended),
        EP.episode_facts(submission)])
    entry = parameters["error_submission_rate"]
    assert entry["denominator"] == 1
    assert entry["numerator"] == 1, "the one submission the oracle judged, and failed"
    assert entry["value"] <= 1.0


def test_the_two_detection_errors_are_not_about_submissions():
    """They collapsed onto each other and reported a confident zero.

    Both were "declared completion and the oracle disagreed" over submissions, which
    is one expression written twice, and is what error_submission_rate asks. A false
    positive needs an episode with nothing wrong to be wrong about; a false negative
    needs the subject to say it found nothing. Neither population exists in a
    fault-only campaign, and neither may render as a measured zero.
    """
    parameters = EP.parameters_for([EP.episode_facts(episode()) for _ in range(4)])

    assert parameters["false_positive_rate"]["status"] == "no_denominator"
    assert parameters["false_positive_rate"]["value"] is None
    assert "--no-fault" in parameters["false_positive_rate"]["reason"]

    assert parameters["false_negative_rate"]["status"] == "unavailable"
    assert parameters["false_negative_rate"]["value"] is None

    assert (parameters["false_positive_rate"]["numerator"],
            parameters["false_positive_rate"]["denominator"]) != \
           (parameters["error_submission_rate"]["numerator"],
            parameters["error_submission_rate"]["denominator"]), \
        "the two must no longer be the same expression"


def test_a_no_fault_episode_is_what_gives_the_false_positive_rate_a_denominator():
    """Test parameter A8 creates the population; until it runs there is none."""
    healthy = episode()
    healthy["metrics"] = {"fault_applicable": False, "false_positive": True}
    clean = episode()
    clean["metrics"] = {"fault_applicable": False, "false_positive": False}
    fault = episode()
    fault["metrics"] = {"fault_applicable": True, "false_positive": None}

    entry = EP.parameters_for([EP.episode_facts(d) for d in (healthy, clean, fault)]
                              )["false_positive_rate"]
    assert entry["status"] == "measured"
    assert (entry["numerator"], entry["denominator"]) == (1, 2), \
        "the fault episode was never asked the question and is out of the denominator"


def test_an_episode_the_repair_oracle_never_judged_is_not_one_the_subject_failed():
    """`repair_passed is None` was read as a definite failure.

    The oracle is assigned after the subject returns, so a judge-side error between
    the two leaves a full `sut_result` carrying no repair verdict at all.
    """
    unjudged = episode()
    unjudged["evaluations"] = {}
    judged = episode()
    judged["evaluations"] = {"repair": {"passed": True}}

    parameters = EP.parameters_for([EP.episode_facts(unjudged), EP.episode_facts(judged)])
    assert (parameters["pass_rate"]["numerator"],
            parameters["pass_rate"]["denominator"]) == (1, 1), \
        "one episode was judged and it passed; the other was never judged"
    assert parameters["error_submission_rate"]["denominator"] == 1


def test_a_report_with_no_operation_log_says_nothing_about_early_submission():
    """The denominator does not come from the log, so the numerator's absence lied.

    A subject whose report carries no `ani_operations` kept its place in the
    denominator while its validation count read zero, and the rate asserted every
    submission early from a record that says nothing about validation either way.
    """
    logless = episode()
    del logless["sut_result"]["ani_operations"]

    entry = EP.parameters_for([EP.episode_facts(logless)])["early_submission_rate"]
    assert entry["status"] == "no_denominator", entry
    assert entry["value"] is None


def test_parameter_thirteen_says_it_is_a_proxy():
    """The excel asks for the LLM's explanation against the scenario's methods."""
    entry = EP.parameters_for([EP.episode_facts(episode())])["llm_found_problem_rate"]
    assert entry["proxy_for"], entry
    assert entry["compiler_imported"] is EP.REPLAY_IMPORTED


def test_an_environment_that_cannot_replay_says_so_instead_of_reporting_zero(monkeypatch):
    """The import failure was folded into the per-episode failure path.

    Breaking the compiler turned 15/138 into `measured 0.000 0/99` with the corpus
    digest and every other flag reading healthy.
    """
    monkeypatch.setattr(EP, "REPLAY_IMPORTED", False)
    monkeypatch.setattr(EP, "REPLAY_IMPORT_ERROR", "ImportError: simulated")
    edited = episode([op("update_config", arguments={"changes": [{"target": "leaf1"}]})])

    entry = EP.parameters_for([EP.episode_facts(edited)])["llm_found_problem_rate"]
    assert entry["status"] == "no_denominator"
    assert entry["value"] is None
    assert "simulated" in entry["reason"]
    assert entry["compiler_imported"] is False


def test_the_topology_cache_answers_per_root(tmp_path):
    """One shared dict served a second root's lookups from the first root's files."""
    EP._TOPOLOGY_BY_DIGEST.clear()
    real = EP.topology_by_digest(EP.REPO_ROOT)
    empty = EP.topology_by_digest(tmp_path)
    assert real, "the repo has topology descriptors"
    assert empty == {}, "a root with no scenarios/topologies knows no digest"
    assert EP.topology_by_digest(EP.REPO_ROOT) == real


def test_a_tool_name_the_model_invented_is_named_not_dropped():
    facts = EP.episode_facts(episode([op("list_vlans", error="unsupported ANI operation")]))
    assert facts.buckets["unknown"] == 1
    assert EP.action_bucket("list_vlans") == "unknown"


def test_wait_is_structurally_zero_rather_than_a_rate():
    parameters = EP.parameters_for([EP.episode_facts(episode([op("get_topology")]))])
    wait = parameters["ani_call_type_ratio"]["wait"]
    assert wait["status"] == "structurally_zero"
    assert wait["value"] == 0.0
    assert EP.WAIT_OPERATIONS == ()


def test_an_uncapped_campaign_reports_no_denominator_not_zero():
    uncapped = EP.episode_facts(episode(
        [op("get_topology")],
        sut_result__execution={"termination": {"cause": "own_conclusion"},
                               "interaction_limits": {"ani": {"limit": None, "reached": False}}}))
    entry = EP.parameters_for([uncapped])["interaction_limit_rate"]
    assert entry["status"] == "no_denominator"
    assert entry["value"] is None, "a null is a refusal to answer; 0.0 would be a claim"

    capped = EP.episode_facts(episode(
        [op("get_topology")],
        sut_result__execution={"termination": {"cause": "own_conclusion"},
                               "interaction_limits": {"ani": {"limit": 6, "reached": True}}}))
    measured = EP.parameters_for([capped])["interaction_limit_rate"]
    assert measured["status"] == "measured"
    assert (measured["numerator"], measured["denominator"]) == (1, 1)


def test_a_completion_the_gate_refused_until_the_clock_ran_out_is_a_time_limit():
    facts = EP.episode_facts(episode(
        [op("get_topology")],
        sut_result__execution={"termination": {"cause": "completion_rejected_until_budget"}}))
    assert facts.hit_time_limit is True


def test_an_episode_the_subject_never_ran_is_not_a_scored_zero():
    """A judge-side failure leaves `sut_result` an empty object, not a subject result."""
    document = episode()
    document["sut_result"] = {}
    facts = EP.episode_facts(document)
    assert facts.scored is False
    parameters = EP.parameters_for([facts])
    assert parameters["pass_rate"]["status"] == "no_denominator"
    assert parameters["score"]["value"] is None


def test_every_unavailable_entry_refuses_to_produce_a_number():
    parameters = EP.parameters_for([EP.episode_facts(episode([op("get_topology")]))])
    for key, entry in parameters.items():
        if key == "ani_call_type_ratio":
            entry = entry["search"]
        assert entry["status"] in {"measured", "no_denominator", "structurally_zero",
                                   "unavailable"}
        if entry["status"] in {"no_denominator", "unavailable"}:
            assert entry["value"] is None
            assert entry["reason"]


def test_a_fault_binding_that_cannot_be_replayed_is_not_a_miss():
    """A topology digest this checkout cannot produce must refuse, not score a zero."""
    document = episode([op("update_config", arguments={"changes": [{"target": "leaf1"}]})])
    document["scenario"] = {"id": "connectivity.disable_interface.m1"}
    document["provenance"] = {"scenario_seed": 1, "topology_sha256": "0" * 64}
    facts = EP.episode_facts(document)
    assert facts.edited_any_device is True
    assert facts.fault_located is None, "an unrecoverable binding is not a wrong device"
    assert EP.parameters_for([facts])["llm_found_problem_rate"]["status"] == "no_denominator"


def test_an_episode_that_changed_no_device_did_not_find_the_fault():
    """It is a miss, not an unmeasurable episode: nothing was located."""
    facts = EP.episode_facts(episode([op("get_topology")]))
    assert facts.edited_any_device is False
    assert facts.fault_located is False

    parameters = EP.parameters_for([facts])
    assert (parameters["llm_found_problem_rate"]["numerator"],
            parameters["llm_found_problem_rate"]["denominator"]) == (0, 1)


def test_the_stricter_device_write_reading_stays_beside_the_rate():
    pushed = episode([op("update_config", arguments={
        "changes": [{"target": "leaf1", "commands": ["x"]}]})])
    entry = EP.parameters_for([EP.episode_facts(pushed)])["llm_found_problem_rate"]
    assert "device_write_numerator" in entry


def test_a_runs_dir_that_does_not_exist_is_an_error_not_an_empty_report(tmp_path):
    """Otherwise a mistyped path reports "0 files, 0 scored" and exits 0."""
    with pytest.raises(SystemExit):
        EP.main([str(tmp_path / "no-such-campaign")])


def test_the_parameters_group_by_intent_through_the_summary_key(tmp_path):
    """`--by subject,intent` needs nothing here: the cell key comes from the summary."""
    results = tmp_path / "x" / "results"
    results.mkdir(parents=True)
    for intent in ("low", "high"):
        document = episode()
        document["provenance"] = {"intent_variant": intent}
        (results / f"{intent}.json").write_text(json.dumps(document), encoding="utf-8")

    report = EP.build_parameters(tmp_path, EP.summarize_campaign.parse_by("subject,intent"))
    assert [(c["subject"], c["intent"]) for c in report["cells"]] == \
        [("unknown", "high"), ("unknown", "low")]
    assert EP.main([str(tmp_path), "--by", "subject,intent", "--json"]) == 0


def test_every_ani_operation_lands_in_a_bucket():
    """A verb the ANI offers must be classified, or the histogram hides it.

    `action_bucket` reads names, so an operation added to the ANI and not added here
    falls into `unknown` without a word. That is how `get_object` and `update_object`
    arrived with ANI v0.2: the one configuration change of an episode was reported as
    `unknown`, and a reader of the histogram could not see a device had been written.
    """
    module = _load()
    from benchmarks.platforms.containerlab.ani import ANI_OPERATIONS

    unclassified = [name for name in ANI_OPERATIONS
                    if module.action_bucket(name) == "unknown"]
    assert unclassified == [], (
        "these ANI operations are in no bucket and would be reported as 'unknown': "
        f"{unclassified}. Add each to DEVICE_READS, CONFIG_WRITES or "
        "VALIDATE_OPERATIONS in scripts/evaluation_parameters.py.")


def test_the_ani_write_verbs_are_reported_as_configuration():
    """The buckets must separate reading a device from changing one."""
    module = _load()

    assert module.action_bucket("get_object") == "check_config"
    assert module.action_bucket("update_object") == "apply_config"
    assert module.action_bucket("update_config") == "apply_config"
    assert module.action_bucket("rollback_config") == "apply_config"


# --- parameter 13, with the judge's verdict in the record ---------------------------

def _judged(found, source="final_response", **overrides):
    return episode(metrics__diagnosis={"found": found, "hypothesis_source": source,
                                       "score": 1.5 if found else -2.0}, **overrides)


def test_parameter_thirteen_reads_the_judges_verdict_when_the_record_carries_it():
    facts = [EP.episode_facts(_judged(True)), EP.episode_facts(_judged(False, "termination_turn")),
             EP.episode_facts(_judged(True, "summary"))]
    entry = EP.parameters_for(facts)["llm_found_problem_rate"]
    assert entry["status"] == "measured"
    assert (entry["numerator"], entry["denominator"]) == (2, 3)
    assert entry["hypothesis_sources"] == {"final_response": 1, "termination_turn": 1, "summary": 1}
    assert "ParaPLUIE" in entry["method"]
    assert "proxy_for" not in entry, "the real reading is no longer labelled a proxy"
    # The device-based reading stays beside it, still labelled as the proxy it is.
    assert entry["proxy"]["proxy_for"]
    assert entry["unjudged"] == 0


def test_an_unjudged_episode_stays_out_of_the_denominator_but_is_counted():
    unjudged = episode(metrics__diagnosis={"found": None, "reason": "no diagnosis judge configured"})
    facts = [EP.episode_facts(_judged(True)), EP.episode_facts(unjudged), EP.episode_facts(episode())]
    entry = EP.parameters_for(facts)["llm_found_problem_rate"]
    assert (entry["numerator"], entry["denominator"]) == (1, 1)
    assert entry["unjudged"] == 2


def test_without_any_verdict_the_proxy_is_the_whole_answer():
    facts = [EP.episode_facts(episode(metrics__diagnosis={"found": None, "reason": "no judge"}))]
    entry = EP.parameters_for(facts)["llm_found_problem_rate"]
    assert entry["proxy_for"] and "proxy" not in entry


def test_the_compiled_instance_replay_carries_the_method_for_the_offline_scorer():
    """`fault_target` reads its device from the same replay the offline scorer uses."""
    records = sorted(CAMPAIGN.glob("**/results/*.json"))
    if not records:
        pytest.skip("the recorded campaign is not on this machine")
    document = json.loads(records[0].read_text(encoding="utf-8"))
    instance = EP.compiled_instance(document)
    if instance is None:
        pytest.skip("the record's topology digest does not match this checkout")
    method = instance["private"]["evaluator"]["selected_method"]
    assert method["name"] and method["operation"]["name"]
    assert EP.fault_target(document) == instance["private"]["bindings"]["target"]
