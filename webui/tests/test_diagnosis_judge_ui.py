"""The diagnosis judge (evaluation parameter 13) on its way through the interface.

A judge is chosen among the registered endpoints' models, named to the judge
process with its base URL, given its key through the environment, refused by name
when no endpoint serves it, and read back out of the record into the verdict rows.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui.app import app
from webui.argv import build_benchmark_argv, build_benchmark_env
from webui.campaign import CampaignRequest, ScenarioSelection, episode as cell_episode, plan
from webui.catalog import preset
from webui.endpoints import Endpoint
from webui.form import EpisodeRequest
from webui.orchestrator.results import verdict_from_result

SMOKE = preset("connectivity-smoke")
JUDGE = Endpoint(id="local-relay", name="local relay", api_base="http://127.0.0.1:18002/v1",
                 models=("ministral-3-8b-instruct",), api_key="secret")


def episode(**overrides) -> EpisodeRequest:
    return EpisodeRequest(**{
        "architecture": "langchain_agent", "experiment": "connectivity-smoke",
        "scenario_id": "connectivity.disable_interface.m1", "model": "openai/gpt-4o-mini",
        "seed": 9, "execution_budget_seconds": 400, **overrides,
    })


def pairs(argv: list[str]) -> dict[str, str]:
    return {argv[index]: argv[index + 1] for index in range(0, len(argv) - 1)
            if argv[index].startswith("--")}


def judge_argv(request: EpisodeRequest, judge: Endpoint | None) -> list[str]:
    return build_benchmark_argv(
        request, SMOKE, experiment_id="webui-run", sut_url="http://127.0.0.1:8003",
        result_dir="reports/manual/results", report_dir="reports/manual", judge=judge)


def test_the_judge_is_named_with_its_endpoint_and_its_key_stays_off_the_command_line():
    argv = judge_argv(episode(diagnosis_judge_model="ministral-3-8b-instruct"), JUDGE)
    values = pairs(argv)
    assert values["--diagnosis-judge-model"] == "ministral-3-8b-instruct"
    assert values["--diagnosis-judge-api-base"] == "http://127.0.0.1:18002/v1"
    assert "secret" not in " ".join(argv)
    assert build_benchmark_env(JUDGE) == {"IBN_DIAGNOSIS_JUDGE_API_KEY": "secret"}


def test_no_judge_means_no_judge_flags_and_no_key():
    argv = judge_argv(episode(), None)
    assert "--diagnosis-judge-model" not in argv
    assert build_benchmark_env(None) == {}
    keyless = Endpoint(id="x", name="x", api_base="http://x/v1", models=("m",))
    assert build_benchmark_env(keyless) == {}


def test_a_judge_no_endpoint_serves_is_refused_by_name():
    try:
        judge_argv(episode(diagnosis_judge_model="nobody/serves-this"), None)
    except ValueError as exc:
        assert "nobody/serves-this" in str(exc) and "Models page" in str(exc)
    else:
        raise AssertionError("a judge without an endpoint was accepted")


def test_every_cell_of_a_campaign_inherits_the_judge():
    request = CampaignRequest(
        name="matrix", architectures=["langchain_agent"], models=["openai/gpt-4o-mini"],
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1", seed=9)],
        execution_budget_seconds=400, diagnosis_judge_model="ministral-3-8b-instruct")
    cells = plan(request)
    assert cells and all(
        cell_episode(request, cell).diagnosis_judge_model == "ministral-3-8b-instruct" for cell in cells)


def test_the_verdict_rows_read_the_judged_diagnosis_out_of_the_record():
    verdict = verdict_from_result({
        "metrics": {"success": True, "diagnosis": {
            "found": True, "score": 4.25, "hypothesis": "web1 had no default route",
            "hypothesis_source": "final_response",
            "judge": {"model": "ministral-3-8b-instruct", "api_base": "http://127.0.0.1:18002/v1"}}},
        "provenance": {"configured_model": "m"},
    })
    keys = list(verdict)
    assert keys.index("root_cause_identified") == keys.index("provider_reported_model") + 1
    assert verdict["root_cause_identified"] is True and verdict["diagnosis_score"] == 4.25
    assert verdict["sut_diagnosis"] == "web1 had no default route"
    assert verdict["diagnosis_judge"] == "ministral-3-8b-instruct"


def test_an_unjudged_record_shows_the_rows_as_unanswered():
    verdict = verdict_from_result({"metrics": {"success": False}, "provenance": {}})
    assert verdict["root_cause_identified"] is None and verdict["diagnosis_score"] is None
    assert verdict["sut_diagnosis"] is None and verdict["diagnosis_judge"] is None


def test_validating_with_an_unregistered_judge_is_refused_before_anything_starts(tmp_path, monkeypatch):
    from webui import endpoints

    monkeypatch.setattr(endpoints, "STORE", tmp_path / "models.json")
    client = TestClient(app)
    response = client.post("/api/validate", json={
        "architecture": "langchain_agent", "experiment": "connectivity-smoke",
        "scenario_id": "connectivity.disable_interface.m1", "model": "openai/gpt-4o-mini",
        "seed": 9, "execution_budget_seconds": 400, "diagnosis_judge_model": "not/registered",
    })
    assert response.status_code == 400
    assert "not/registered" in response.json()["detail"]

    client.put("/api/endpoints", json={
        "name": "local relay", "api_base": "http://127.0.0.1:18002/v1",
        "models": ["ministral-3-8b-instruct"]})
    response = client.post("/api/validate", json={
        "architecture": "langchain_agent", "experiment": "connectivity-smoke",
        "scenario_id": "connectivity.disable_interface.m1", "model": "openai/gpt-4o-mini",
        "seed": 9, "execution_budget_seconds": 400, "diagnosis_judge_model": "ministral-3-8b-instruct",
    })
    assert response.status_code == 200, response.text
    assert response.json()["diagnosis_judge"] == {
        "model": "ministral-3-8b-instruct", "api_base": "http://127.0.0.1:18002/v1"}


def test_the_reading_is_passed_on_and_defaults_to_the_exact_one():
    with_reading = pairs(judge_argv(
        episode(diagnosis_judge_model="gpt-4.1", diagnosis_judge_reading="top_logprobs"), JUDGE))
    assert with_reading["--diagnosis-judge-reading"] == "top_logprobs"
    plain = judge_argv(episode(diagnosis_judge_model="ministral-3-8b-instruct"), JUDGE)
    assert "--diagnosis-judge-reading" not in plain


def test_a_judge_no_endpoint_serves_runs_on_the_subjects_endpoint(tmp_path, monkeypatch):
    from webui import endpoints
    from webui.orchestrator.episode_runner import diagnosis_judge

    monkeypatch.setattr(endpoints, "STORE", tmp_path / "models.json")
    endpoints.put(Endpoint(id="openai", name="OpenAI", api_base="https://api.openai.com/v1",
                           models=("gpt-5.4",), api_key="secret"))
    judge = diagnosis_judge(episode(model="gpt-5.4", diagnosis_judge_model="gpt-4.1"))
    assert judge.api_base == "https://api.openai.com/v1" and judge.api_key == "secret"
    argv = judge_argv(episode(model="gpt-5.4", diagnosis_judge_model="gpt-4.1",
                              diagnosis_judge_reading="top_logprobs"), judge)
    assert pairs(argv)["--diagnosis-judge-api-base"] == "https://api.openai.com/v1"
    assert "secret" not in " ".join(argv)
    with pytest.raises(ValueError):
        diagnosis_judge(episode(model="nobody/registered", diagnosis_judge_model="gpt-4.1"))
    assert diagnosis_judge(episode(model="gpt-5.4")) is None


def test_the_campaign_carries_the_reading_to_every_cell():
    request = CampaignRequest(
        name="matrix", architectures=["langchain_agent"], models=["gpt-5.4"],
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1", seed=9)],
        execution_budget_seconds=400, diagnosis_judge_model="gpt-4.1", diagnosis_judge_reading="top_logprobs")
    assert all(cell_episode(request, cell).diagnosis_judge_reading == "top_logprobs" for cell in plan(request))
