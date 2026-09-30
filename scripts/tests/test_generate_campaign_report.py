"""The campaign page compares the subjects on the same plates and shows every episode.

A campaign exists to put subjects side by side on the same faults; the per-episode
report cannot, since it reads one episode. These hold the campaign page to the
comparison it is for, and to the episodes it is read from.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks.core.reporting import validate_result

ROOT = Path(__file__).resolve().parents[2]
MODEL = "openai/fr-gpt-5.4"
BASELINE = "LangChain ANI Baseline"
RAG = "LangChain RAG ANI Agent"
CATEGORY = {"get_topology": "read", "get_object": "read", "update_object": "mutation",
            "execute_validation": "validation"}
PHASES = ("load_configuration", "load_validate_scenario", "load_oracles", "deploy_reset_testbed",
          "apply_reference_state", "evaluate_healthy", "record_baseline", "inject_fault",
          "evaluate_degradation", "invoke_sut", "evaluate_repair_preservation", "calculate_metrics",
          "write_result", "restore_destroy")


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


campaign_report = _load("generate_campaign_report")
generate_report = _load("generate_report")


def result(*, subject: str, scenario: str, seed: int, repaired: bool, run_id: str,
           rag: dict[str, Any] | None = None) -> dict[str, Any]:
    """A judge record of the current schema, as small as the schema allows."""
    calls = ("get_topology", "get_object", "update_object", "execute_validation")
    operations = [{"operation": name, "category": CATEGORY[name], "ok": True, "duration_seconds": 1.0,
                   "arguments": {"node": "leaf2", "name": "ethernet-1/2"}, "error": None} for name in calls]
    parameters = {"model": MODEL, "prompt_variant": "default", "max_tokens": 400,
                  "max_execution_seconds": 400.0, "ani_call_limit": None}
    status = "completed" if repaired else "failed"
    execution = {
        "budget_seconds": 400.0, "elapsed_seconds": 120.0 if repaired else 300.0, "llm_calls": 4,
        "termination": {"cause": "own_conclusion", "detail": status, "turn": 4},
        "token_usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        "model_turns": [{"turn": turn, "seconds": 2.0, "kind": "tool_calls", "content_chars": 0,
                         "reasoning_chars": 0, "tool_calls": [name], "operations": [turn], "error": None}
                        for turn, name in enumerate(calls, start=1)],
        "model_parameters": parameters,
        "trace_ref": None,
    }
    if rag is not None:
        execution["rag"] = rag
    document = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": "2026-09-24T12:00:00+00:00",
        "status": "completed",
        "experiment_id": run_id.rsplit("-", 1)[0],
        "scenario": {"id": scenario, "version": "1.0.0", "domain": scenario.split(".")[0],
                     "sut_task_id": "sut-" + hashlib.md5(run_id.encode()).hexdigest()},
        "provenance": {
            "configured_model": MODEL, "sut_reported_model": MODEL, "provider_reported_model": MODEL,
            "a2a_sdk_version": "1.1.2", "sut_identity": subject, "sut_version": "1.0.0",
            "model_parameters": copy.deepcopy(parameters),
            "git": {"commit": "0" * 40, "dirty": False},
            "scenario_version": "1.0.0",
            "oracle_versions": {"healthy": "1.0.0", "expected_degradation": "1.0.0", "repair": "1.0.0"},
            "topology_sha256": "0" * 64, "reference_state_sha256": "0" * 64, "scenario_seed": seed,
        },
        "operation_counts": {"llm_calls": 4, "ani_reads": 2, "ani_mutations": 1, "validations": 1,
                             "successful_mutations": 1, "failed_operations": 0, "unsafe_operations": 0,
                             "input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        "phase_durations": {phase: 125.0 if phase == "invoke_sut" else 1.0 for phase in PHASES},
        "evaluations": {phase: {"oracle_id": f"connectivity.path.{phase}", "version": "1.0.0",
                                "phase": phase, "passed": repaired if phase == "repair" else True}
                        for phase in ("healthy", "expected_degradation", "repair", "preservation")},
        "metrics": {"success": repaired, "fault_applicable": True, "repair_score": 1.0 if repaired else 0.0},
        "sut_result": {
            "mode": "self_execute", "status": status, "verified": repaired, "device_changes": [],
            "ani_operations": operations, "execution": execution,
            "final_response": {"status": status, "summary": f"{subject} on {scenario}: {status}"},
        },
        "cleanup_result": {}, "convergence_result": {}, "error": None,
    }
    validate_result(document)
    return document


RETRIEVAL = {"searches": [{"origin": "context", "query": "q", "top_k": 4, "seconds": 0.0, "results": [],
                           "error": None}],
             "tool_searches": 2, "context_excerpts": 4}

#: Two subjects on the same two plates, each repairing the plate the other did not, and a
#: cell the campaign never reached.
PLAN = (
    ("langchain_agent", BASELINE, "connectivity.disable_interface.m1", 11, True),
    ("langchain_agent", BASELINE, "connectivity.remove_ip.m1", 22, False),
    ("langchain_rag_agent", RAG, "connectivity.disable_interface.m1", 11, False),
    ("langchain_rag_agent", RAG, "connectivity.remove_ip.m1", 22, True),
    ("langchain_rag_agent", RAG, "qos.wan_shaping_policy_repair.m2", 33, None),
)


def build_campaign(root: Path, plan=PLAN) -> Path:
    """A campaign directory laid out the way the web interface writes one."""
    (root / "reports").mkdir(parents=True)
    cells = []
    for index, (architecture, subject, scenario, seed, repaired) in enumerate(plan):
        run_id = f"{architecture}-{scenario.replace('.', '-')}-seed{seed}-run{index}"
        cell = {"index": index, "architecture": architecture, "model": MODEL,
                "experiment": "connectivity-smoke", "scenario_id": scenario, "no_fault": False,
                "seed": seed, "experiment_id": run_id.rsplit("-", 1)[0]}
        if repaired is None:
            cells.append({**cell, "state": "skipped", "verdict": None, "result_path": None,
                          "report_path": None, "error": "not attempted: the campaign stopped first"})
            continue
        path = root / f"{run_id}.json"
        path.write_text(json.dumps(result(subject=subject, scenario=scenario, seed=seed, repaired=repaired,
                                          run_id=run_id, rag=RETRIEVAL if subject == RAG else None)),
                        encoding="utf-8")
        page = next(written for written in generate_report.write_report(
            path, output=root / "reports" / f"{run_id}.report") if written.suffix == ".html")
        cells.append({**cell, "state": "done", "verdict": {"benchmark_success": repaired},
                      "result_path": str(path), "report_path": str(page), "error": None})
    manifest = {"campaign_id": root.name, "started_at": "2026-09-24T12:00:00+00:00", "finished_at": None,
                "state": "done", "error": None, "counts": {"total": len(cells)}, "report_path": None,
                "request": {"architectures": ["langchain_agent", "langchain_rag_agent"], "models": [MODEL],
                            "execution_budget_seconds": 400},
                "cells": cells}
    (root / "campaign.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture
def campaign(tmp_path: Path) -> Path:
    return build_campaign(tmp_path / "rag-vs-baseline")


def written(campaign: Path, **options) -> tuple[dict[str, Any], str]:
    paths = campaign_report.write_campaign_report(campaign, **options)
    by_suffix = {path.suffix: path for path in paths}
    return (json.loads(by_suffix[".json"].read_text(encoding="utf-8")),
            by_suffix[".html"].read_text(encoding="utf-8"))


def labels(document: dict[str, Any]) -> dict[str, str]:
    return {contender["key"]: contender["label"] for contender in document["contenders"]}


def test_the_page_is_written_beside_the_episodes_reports(campaign):
    paths = campaign_report.write_campaign_report(campaign)
    assert sorted(path.name for path in paths) == ["campaign.report.html", "campaign.report.json"]
    assert {path.parent for path in paths} == {campaign / "reports"}
    assert not list(campaign.glob("*.report.json")), "nothing beside the results, which are collected as *.json"


def test_each_subject_is_one_row_named_by_its_card_and_model(campaign):
    document, _ = written(campaign)
    assert [contender["label"] for contender in document["contenders"]] == [
        f"{BASELINE} · fr-gpt-5.4", f"{RAG} · fr-gpt-5.4"]
    overview = {labels(document)[row["contender"]]: row for row in document["comparison"]["overview"]}
    assert {name: (row["repair_passed"], row["scored"]) for name, row in overview.items()} == {
        f"{BASELINE} · fr-gpt-5.4": (1, 2), f"{RAG} · fr-gpt-5.4": (1, 2)}
    assert overview[f"{BASELINE} · fr-gpt-5.4"]["termination"] == {"own_conclusion": 2}


def test_the_same_plate_is_compared_across_subjects(campaign):
    """Same repair count, different plates: only the plate-by-plate table shows it."""
    document, html = written(campaign)
    names = labels(document)
    by_plate = {(row["scenario"], row["seed"]): {names[key]: value["repaired"]
                                                for key, value in row["outcomes"].items()}
                for row in document["comparison"]["plates"]}
    assert by_plate == {
        ("connectivity.disable_interface.m1", 11): {f"{BASELINE} · fr-gpt-5.4": True, f"{RAG} · fr-gpt-5.4": False},
        ("connectivity.remove_ip.m1", 22): {f"{BASELINE} · fr-gpt-5.4": False, f"{RAG} · fr-gpt-5.4": True},
    }
    [pair] = document["comparison"]["pairs"]
    assert (pair["compared"], pair["both"], pair["only_a"], pair["only_b"], pair["neither"]) == (2, 0, 1, 1, 0)
    assert "Who repaired what the other did not" in html


def test_repairs_are_counted_by_family_as_the_campaign_readmes_do(campaign):
    document, _ = written(campaign)
    families = {row["family"]: {labels(document)[key]: (value["repaired"], value["scored"])
                                for key, value in row["repairs"].items()}
                for row in document["comparison"]["families"]}
    assert families["connectivity.disable_interface"] == {
        f"{BASELINE} · fr-gpt-5.4": (1, 1), f"{RAG} · fr-gpt-5.4": (0, 1)}


def test_the_rates_are_the_evaluation_parameters_of_each_subject(campaign):
    document, html = written(campaign)
    for parameters in document["comparison"]["parameters"].values():
        assert (parameters["pass_rate"]["numerator"], parameters["pass_rate"]["denominator"]) == (1, 2)
    assert "tool_call_success_rate" in html
    assert re.search(r"0\.500\s+1/2", html), "written as evaluation_parameters.py writes it"


def test_retrieval_is_reported_for_the_subject_that_retrieves_only(campaign):
    document, html = written(campaign)
    retrieval = {labels(document)[key]: value for key, value in document["comparison"]["retrieval"].items()}
    assert list(retrieval) == [f"{RAG} · fr-gpt-5.4"]
    assert retrieval[f"{RAG} · fr-gpt-5.4"]["tool_searches"] == 4
    assert "Document retrieval" in html


def test_every_cell_is_listed_in_matrix_order_and_linked_to_its_own_report(campaign):
    document, html = written(campaign)
    episodes = document["episodes"]
    assert [row["number"] for row in episodes] == [1, 2, 3, 4, 5]
    skipped = episodes[-1]
    assert skipped["state"] == "skipped" and "not attempted" in skipped["error"]
    assert skipped["label"] == f"{RAG} · fr-gpt-5.4", "named as the subject names itself"
    for row in episodes[:4]:
        assert (campaign / "reports" / row["report"]).is_file(), "links are relative to the page"
        assert f'href="{row["report"]}"' in html


def test_the_interactions_are_each_episodes_timeline(campaign):
    _, html = written(campaign)
    for number in (1, 2, 3, 4):
        assert f'<details id="episode-{number}">' in html
    assert 'id="episode-5"' not in html, "a cell that ran nothing has no interactions"
    assert html.count("Thinking and calls, in order") == 4
    assert "ethernet-1/2" in html, "the arguments the model chose, as its own report shows them"


def test_an_unreadable_result_is_listed_and_the_rest_still_compared(campaign):
    manifest = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    Path(manifest["cells"][0]["result_path"]).write_text("{truncated", encoding="utf-8")
    document, html = written(campaign)
    assert [item["path"] for item in document["source"]["broken"]] == [manifest["cells"][0]["result_path"]]
    assert document["totals"]["results"] == 3
    assert "Unreadable files" in html


def test_the_manifest_given_wins_over_the_file(campaign):
    """The web interface writes the page before campaign.json says the campaign ended."""
    manifest = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    document, html = written(campaign, manifest={**manifest, "error": "campaign stopped: model mismatch"})
    assert document["campaign"]["error"] == "campaign stopped: model mismatch"
    assert "campaign stopped: model mismatch" in html


def test_a_directory_the_web_interface_did_not_write_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit):
        campaign_report.main([str(tmp_path)])
    assert "campaign.json" in capsys.readouterr().err
