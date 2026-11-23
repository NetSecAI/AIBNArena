"""Thinking is declared with the model, on the Models page, and nowhere else.

The declaration is stored with the endpoint, checked against the model's family when
it is saved, shown wherever the model is chosen, and written into the request of every
launch -- an episode's fields, a campaign's `model_thinking` -- so the record states
what the subject was asked.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webui.routes as routes
from webui import endpoints
from webui.app import app
from webui.campaign import CampaignRequest, Cell, ScenarioSelection, cell_thinking, episode
from webui.endpoints import (
    Endpoint, check_thinking, clean_thinking, load, openai_endpoint, save, thinking_of, with_thinking,
)
from webui.form import EpisodeRequest
from webui.orchestrator import MANAGER

client = TestClient(app)

EPISODE = {
    "architecture": "langchain_agent",
    "experiment": "connectivity-smoke",
    "scenario_id": "connectivity.disable_interface.m1",
    "model": "openai/gpt-5-mini",
    "seed": 9,
    "execution_budget_seconds": 400,
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    monkeypatch.setattr(endpoints, "STORE", path)
    return path


def _declare(store, thinking):
    save([openai_endpoint("openai", "OpenAI", ("gpt-5-mini", "gpt-4o-mini"), "sk-test", thinking)], store)


def test_a_declaration_survives_a_round_trip_and_reaches_the_browser(store):
    _declare(store, {"gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"},
                     "gpt-4o-mini": {"enable_thinking": None}, "gpt-9": {"enable_thinking": True}})
    stored = load(store)[0]
    # Keyed as the subject is given the model; an empty declaration and a model the
    # endpoint does not serve are not kept.
    assert stored.thinking == {"openai/gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}}
    assert stored.public()["thinking"] == stored.thinking
    assert "sk-test" not in str(stored.public())
    assert thinking_of("openai/gpt-5-mini", store) == {"enable_thinking": True, "thinking_effort": "medium"}
    assert thinking_of("openai/gpt-4o-mini", store) == {} and thinking_of("unregistered", store) == {}


def test_an_entry_stored_before_thinking_existed_has_none():
    assert Endpoint(id="x", name="x", api_base="http://x/v1").thinking == {}


def test_a_declaration_is_cleaned_and_checked_by_the_subjects_own_rule():
    assert clean_thinking({"enable_thinking": "true", "thinking_effort": "", "thinking_style": None}) == {
        "enable_thinking": True}
    with pytest.raises(ValueError):
        clean_thinking({"enable_thinking": "maybe"})
    check_thinking("openai/gpt-5-mini", {"enable_thinking": True, "thinking_effort": "high"})
    with pytest.raises(ValueError, match="no reasoning switch"):
        check_thinking("ministral-3-8b-instruct", {"enable_thinking": True})
    with pytest.raises(ValueError, match="not an effort level"):
        check_thinking("qwen3-8b", {"enable_thinking": True, "thinking_effort": "high"})
    with pytest.raises(ValueError, match="unknown thinking_effort 'xhigh'"):
        check_thinking("openai/gpt-5-mini", {"enable_thinking": True, "thinking_effort": "xhigh"})


def test_the_models_page_saves_a_declaration_the_family_accepts(store):
    answer = client.put("/api/endpoints", json={
        "provider": "openai", "models": ["gpt-5-mini"], "api_key": "sk-test",
        "thinking": {"gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}}})
    assert answer.status_code == 200, answer.text
    assert answer.json()["thinking"] == {"openai/gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}}


@pytest.mark.parametrize("endpoint,model,settings,reason", [
    ({"name": "local relay", "api_base": "http://127.0.0.1:18002/v1"},
     "ministral-3-8b-instruct", {"enable_thinking": True}, "no reasoning switch"),
    ({"provider": "openai", "api_key": "sk-test"},
     "gpt-5-mini", {"enable_thinking": True, "thinking_effort": "xhigh"}, "unknown thinking_effort 'xhigh'"),
])
def test_the_models_page_refuses_a_declaration_the_family_cannot_honour(store, endpoint, model, settings, reason):
    answer = client.put("/api/endpoints", json={
        **endpoint, "models": [model], "thinking": {model: settings}})
    assert answer.status_code == 400 and reason in answer.json()["detail"]
    assert not store.exists()


def test_a_request_keeps_what_it_states_and_takes_the_declaration_for_the_rest():
    request = EpisodeRequest(**EPISODE, thinking_effort="high")
    filled = with_thinking(request, {"enable_thinking": True, "thinking_effort": "low"})
    assert (filled.enable_thinking, filled.thinking_effort) == (True, "high")
    assert with_thinking(request, {}) is request


def test_an_episode_is_compiled_with_its_models_declaration(store):
    _declare(store, {"gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}})
    preview = client.post("/api/validate", json=EPISODE)
    assert preview.status_code == 200, preview.text
    argv = preview.json()["sut_argv"]
    assert argv[argv.index("--enable-thinking") + 1] == "true"


def test_an_episode_its_model_cannot_think_for_is_refused_before_it_starts(store):
    # A declaration written by hand, past the Models page's own check.
    save([Endpoint(id="proxy", name="proxy", api_base="http://x/v1", models=("ministral-3-8b-instruct",),
                   thinking={"ministral-3-8b-instruct": {"enable_thinking": True}})], store)
    answer = client.post("/api/runs", json={**EPISODE, "model": "ministral-3-8b-instruct"})
    assert answer.status_code == 400 and "no reasoning switch" in answer.json()["detail"]
    assert not MANAGER.busy


def _cell(model: str) -> Cell:
    selection = ScenarioSelection(experiment="connectivity-smoke",
                                  scenario_id="connectivity.disable_interface.m1", seed=9)
    return Cell(index=0, architecture="langchain_agent", model=model, selection=selection, seed=9,
                experiment_id="thinking-cell")


def test_a_cell_takes_its_models_recorded_declaration_unless_the_campaign_states_one():
    request = CampaignRequest(
        architectures=["langchain_agent"], models=["openai/gpt-5-mini", "openai/gpt-4o-mini"],
        scenarios=[{"experiment": "connectivity-smoke", "scenario_id": "connectivity.disable_interface.m1"}],
        execution_budget_seconds=400,
        model_thinking={"openai/gpt-5-mini": {"enable_thinking": True, "thinking_effort": "high"}})
    assert cell_thinking(request, "openai/gpt-5-mini") == {
        "enable_thinking": True, "thinking_style": None, "thinking_effort": "high"}
    assert cell_thinking(request, "openai/gpt-4o-mini") == {
        "enable_thinking": None, "thinking_style": None, "thinking_effort": None}
    cell = episode(request, _cell("openai/gpt-5-mini"))
    assert (cell.enable_thinking, cell.thinking_effort) == (True, "high")
    # A campaign recorded with a setting of its own resumes with it.
    recorded = request.model_copy(update={"thinking_effort": "low"})
    assert episode(recorded, _cell("openai/gpt-5-mini")).thinking_effort == "low"


def test_a_campaign_records_each_models_declaration_at_launch(store, monkeypatch):
    _declare(store, {"gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}})

    async def runner(manager, config, record):
        """Stands in for run_campaign, which would deploy the lab."""

    monkeypatch.setattr(routes, "run_campaign", runner)
    monkeypatch.setenv("IBN_SEED_REGISTRY", str(store.parent / "seed-registry.json"))
    response = client.post("/api/campaigns", json={
        "name": "thinking", "architectures": ["langchain_agent"],
        "models": ["openai/gpt-5-mini", "openai/gpt-4o-mini"],
        "scenarios": [{"experiment": "connectivity-smoke",
                       "scenario_id": "connectivity.disable_interface.m1", "seed": 9}],
        "execution_budget_seconds": 400})
    record = MANAGER.get(response.json().get("id", "")) if response.status_code == 201 else None
    try:
        assert response.status_code == 201, response.text
        assert record.request.model_thinking == {
            "openai/gpt-5-mini": {"enable_thinking": True, "thinking_effort": "medium"}}
    finally:
        if record is not None and not record.state.finished:
            MANAGER.finish(record, error="ended by the test")
    assert not MANAGER.busy
