"""The forms' "Check the judge": one scoring call before anything is deployed."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from webui import endpoints
from webui.app import app
from webui.endpoints import Endpoint, save
from webui.judge_check import JudgeUnavailable, check_judge
from webui.orchestrator import episode_runner

OPENAI = Endpoint(id="openai", name="OpenAI", api_base="https://api.openai.com/v1",
                  models=("openai/gpt-4o-mini",), api_key="sk-test", provider="openai")


def openai_answering(status=200, *, seen=None):
    def handler(request):
        body = json.loads(request.content)
        if seen is not None:
            seen.append((str(request.url), request.headers["authorization"], body))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "invalid model ID"}})
        return httpx.Response(200, json={"choices": [{"logprobs": {"content": [{
            "token": "Yes", "logprob": -0.01,
            "top_logprobs": [{"token": "Yes", "logprob": -0.01}, {"token": "No", "logprob": -4.7}]}]}}]})
    return httpx.MockTransport(handler)


def test_a_judge_that_scores_is_named_with_its_score():
    seen: list = []
    line = check_judge("openai/gpt-4o-mini", "top_logprobs", OPENAI, transport=openai_answering(seen=seen))
    assert line.startswith("diagnosis judge 'gpt-4o-mini' at https://api.openai.com/v1 (top_logprobs) scores: +4.69")
    url, authorization, body = seen[0]
    # As benchmarks/run.py will call it: the model as the endpoint knows it, the endpoint's key.
    assert url == "https://api.openai.com/v1/chat/completions" and authorization == "Bearer sk-test"
    assert body["model"] == "gpt-4o-mini" and body["logprobs"] is True


def test_a_judge_its_endpoint_refuses_stops_the_run_with_the_endpoints_words():
    with pytest.raises(JudgeUnavailable) as caught:
        check_judge("openai/gpt-4o-mini", "top_logprobs", OPENAI, transport=openai_answering(400))
    assert "'gpt-4o-mini'" in str(caught.value) and "invalid model ID" in str(caught.value)
    assert "sk-test" not in str(caught.value)


def test_a_reading_the_endpoint_cannot_serve_is_found_too():
    def no_logprobs(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Yes"}}]})
    with pytest.raises(JudgeUnavailable) as caught:
        check_judge("openai/gpt-4o-mini", "top_logprobs", OPENAI, transport=httpx.MockTransport(no_logprobs))
    assert "log-probabilities" in str(caught.value)


EPISODE = {"architecture": "langchain_agent", "experiment": "connectivity-smoke",
           "scenario_id": "connectivity.disable_interface.m1", "model": "openai/gpt-4o-mini", "seed": 9,
           "execution_budget_seconds": 400, "diagnosis_judge_model": "openai/gpt-4o-mini",
           "diagnosis_judge_reading": "top_logprobs"}


@pytest.fixture
def registered(tmp_path, monkeypatch):
    monkeypatch.setattr(endpoints, "STORE", tmp_path / "models.json")
    save([OPENAI])


def test_validate_checks_the_judge_when_asked_and_refuses_one_that_does_not_score(registered, monkeypatch):
    asked: list = []

    def refuse(model, reading, endpoint):
        asked.append((model, reading, endpoint.id))
        raise JudgeUnavailable("the diagnosis judge 'gpt-4o-mini' did not score a test pair: invalid model ID")

    monkeypatch.setattr(episode_runner, "check_judge", refuse)
    client = TestClient(app)
    answer = client.post("/api/validate", json=EPISODE)
    assert answer.status_code == 400 and "invalid model ID" in answer.json()["detail"]
    assert asked == [("openai/gpt-4o-mini", "top_logprobs", "openai")]

    unchecked = client.post("/api/validate", json={**EPISODE, "check_diagnosis_judge": False})
    assert unchecked.status_code == 200, unchecked.text
    assert unchecked.json()["diagnosis_judge"]["check"] is None and len(asked) == 1


def test_validate_reports_the_check_of_a_judge_that_scores(registered, monkeypatch):
    monkeypatch.setattr(episode_runner, "check_judge", lambda model, reading, endpoint: "diagnosis judge scores: +4.69")
    answer = TestClient(app).post("/api/validate", json=EPISODE)
    assert answer.status_code == 200, answer.text
    assert answer.json()["diagnosis_judge"]["check"] == "diagnosis judge scores: +4.69"


def test_no_judge_means_no_check(registered, monkeypatch):
    monkeypatch.setattr(episode_runner, "check_judge", lambda *args: pytest.fail("the judge was checked"))
    request = {key: value for key, value in EPISODE.items() if not key.startswith("diagnosis_judge")}
    assert TestClient(app).post("/api/validate", json=request).status_code == 200


def test_a_campaign_is_checked_once_at_validate(registered, monkeypatch):
    from webui import routes

    asked: list = []
    monkeypatch.setattr(routes, "check_judge", lambda model, reading, endpoint: asked.append(model) or "scores: +4.69")
    campaign = {"name": "judge check", "architectures": ["langchain_agent", "langchain_rag_agent"],
                "models": ["openai/gpt-4o-mini"], "execution_budget_seconds": 400,
                "scenarios": [{"experiment": "connectivity-smoke", "scenario_id": "connectivity.disable_interface.m1"}],
                "diagnosis_judge_model": "openai/gpt-4o-mini", "diagnosis_judge_reading": "top_logprobs"}
    answer = TestClient(app).post("/api/campaigns/validate", json=campaign)
    assert answer.status_code == 200, answer.text
    assert answer.json()["diagnosis_judge_check"] == "scores: +4.69" and asked == ["openai/gpt-4o-mini"]
