"""Launching a campaign hands the runner the request it was sent.

The route once rebound the request to its own id, so the runner received a string
and every launch failed: a 500 for the page, and a runner task that died reading
`request.seed_campaign`. Nothing tested the launch itself, because a real one
deploys a lab; the runner is replaced here, and everything before it is the real
route.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import webui.routes as routes
from webui import catalog
from webui.app import app
from webui.campaign import CampaignRequest
from webui.form import EpisodeRequest
from webui.orchestrator import MANAGER
from webui.orchestrator.seed_guard import settle

CAMPAIGN = {
    "name": "launch",
    "architectures": ["langchain_agent", "langchain_rag_agent"],
    "models": ["openai/gpt-4o-mini"],
    "scenarios": [{"experiment": "connectivity-smoke",
                   "scenario_id": "connectivity.disable_interface.m1", "seed": 9}],
    "execution_budget_seconds": 400,
}


def test_the_runner_is_handed_the_request_and_the_page_its_seeds(monkeypatch):
    async def runner(manager, config, record):
        """Stands in for run_campaign, which would deploy the lab."""

    monkeypatch.setattr(routes, "run_campaign", runner)
    response = TestClient(app).post("/api/campaigns", json=CAMPAIGN)
    record = MANAGER.get(response.json().get("id", "")) if response.status_code == 201 else None
    try:
        assert response.status_code == 201, response.text
        body = response.json()
        assert isinstance(record.request, CampaignRequest)
        assert record.request.architectures == CAMPAIGN["architectures"]
        assert body["campaign_id"] == record.campaign_id and body["campaign_id"].startswith("launch-")
        assert record.result_dir.endswith(f"/{body['campaign_id']}")
        assert body["seeds"] == [9]
        assert isinstance(body["seed_campaign"], int)
        assert body["cells"] == 2, "one cell per subject on the one scenario row"
        assert record.summary()["request"]["seed_campaign"] == body["seed_campaign"]
    finally:
        if record is not None and not record.state.finished:
            MANAGER.finish(record, error="ended by the test")
    assert not MANAGER.busy


def test_a_row_without_a_seed_draws_one_that_every_subject_then_runs_on(
        monkeypatch, tmp_path):
    """A new campaign draws its own faults, and every subject of it faces the same one.

    The scenario already ran under an earlier campaign: its seed must not be
    borrowed, or two campaigns would silently be one. Within this campaign, the
    drawn seed belongs to the row, so the second subject replays the first one's
    fault rather than a new one.
    """
    monkeypatch.setenv("IBN_SEED_REGISTRY", str(tmp_path / "seed-registry.json"))
    settle(EpisodeRequest(architecture="langchain_agent", experiment="connectivity-smoke",
                          scenario_id="connectivity.disable_interface.m1",
                          model="openai/gpt-4o-mini", execution_budget_seconds=400, seed=9),
           catalog.preset("connectivity-smoke"), run="an-earlier-campaign")

    async def runner(manager, config, record):
        """Stands in for run_campaign, which would deploy the lab."""

    monkeypatch.setattr(routes, "run_campaign", runner)
    seedless = {**CAMPAIGN, "scenarios": [{**CAMPAIGN["scenarios"][0], "seed": None}]}
    response = TestClient(app).post("/api/campaigns", json=seedless)
    record = MANAGER.get(response.json().get("id", "")) if response.status_code == 201 else None
    try:
        assert response.status_code == 201, response.text
        drawn = response.json()["seeds"]
        assert len(drawn) == 1 and drawn[0] not in (None, 9), "a new seed, not the earlier campaign's"
        assert [cell.architecture for cell in record.plan] == CAMPAIGN["architectures"]
        assert {cell.seed for cell in record.plan} == {drawn[0]}, "both subjects on the one fault"
    finally:
        if record is not None and not record.state.finished:
            MANAGER.finish(record, error="ended by the test")
    assert not MANAGER.busy
