"""A campaign that stopped is resumed as the same campaign, on the faults it ran on.

The first run of a matrix can stop before its last cell: a runner bug, a lab
that hung, a server restarted. Launched again as a new campaign, the rest draws
new seeds, and the subjects of the two halves are no longer compared on one
fault. Resuming runs again only the cells that did not end done, in the same
record, on the seeds they were planned with.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import webui.app as app_module
import webui.routes as routes
from webui.campaign import CampaignRequest, ScenarioSelection, plan
from webui.config import WebUIConfig
from webui.orchestrator import (
    CampaignRecord, CellRecord, ResumeRefused, RunManager, RunState, campaign_runner, reopen,
    resume_plan,
)
from webui.orchestrator.campaign_runner import _manifest
from webui.orchestrator.results import collect, results_of

MODEL = "openai/gpt-4o-mini"


def _stopped(states: list[str], *, identifier: str = "20260925T151547Z-demo",
             result_dir: str = "reports/campaigns/runs/demo") -> CampaignRecord:
    """Two subjects on two rows, as the first run left them when it stopped."""
    request = CampaignRequest(
        name="demo", architectures=["langchain_agent", "langchain_rag_agent"], models=[MODEL],
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1", seed=seed)
                   for seed in (9, 17)],
        execution_budget_seconds=400, seed_campaign=4821)
    cells = plan(request)
    record = CampaignRecord(
        id=identifier, request=request, campaign_id="demo-20260925T151547Z", result_dir=result_dir,
        plan=cells, cells=[CellRecord(cell=cell.summary(), state=state)
                           for cell, state in zip(cells, states)],
        state=RunState.FAILED, finished_at="2026-09-25T19:32:21+00:00",
        error="campaign stopped by the test")
    record.events.append({"type": "log", "line": "campaign demo: 4 cells", "source": "launcher"})
    record.events.append({"type": "finished", **record.summary()})
    return record


def test_a_resume_runs_again_what_did_not_end_done_on_the_seeds_the_others_ran_on():
    """The first subject finished; the second failed a cell and never reached the next."""
    record = _stopped(["done", "done", "failed", "skipped"])
    cells = resume_plan(record)
    assert [cell.index for cell in cells] == [2, 3]
    assert {cell.architecture for cell in cells} == {"langchain_rag_agent"}
    assert [cell.seed for cell in cells] == [cell.seed for cell in record.plan[:2]] == [9, 17]


def test_a_subject_that_ran_and_did_not_repair_is_not_run_again():
    """Running a measured failure again until it passes would be choosing the result."""
    record = _stopped(["done", "done", "done", "skipped"])
    record.cells[2].verdict = {"benchmark_success": False}
    assert [cell.index for cell in resume_plan(record)] == [3]


def test_a_campaign_still_running_or_with_every_cell_done_is_not_resumed():
    running = _stopped(["done", "running", "pending", "pending"])
    running.state = RunState.RUNNING_BENCHMARK
    with pytest.raises(ResumeRefused, match="still running"):
        resume_plan(running)
    with pytest.raises(ResumeRefused, match="nothing to resume"):
        resume_plan(_stopped(["done"] * 4))


def test_a_campaign_read_back_from_disk_is_planned_again_and_held_to_its_cells():
    """history.py reads back the cells, not the typed matrix they were planned as."""
    record = _stopped(["done", "done", "failed", "skipped"])
    planned = [cell.experiment_id for cell in record.plan]
    record.plan = []
    assert [cell.index for cell in resume_plan(record)] == [2, 3]
    assert [cell.experiment_id for cell in record.plan] == planned

    moved = _stopped(["done", "done", "failed", "skipped"])
    moved.plan = []
    moved.cells[3].cell["experiment_id"] = "an-experiment-the-catalogue-no-longer-plans"
    with pytest.raises(ResumeRefused, match="no longer plans"):
        resume_plan(moved)


def test_reopening_puts_the_same_campaign_back_in_flight_at_its_address():
    manager = RunManager()
    record = _stopped(["done", "done", "failed", "skipped"])
    record.cells[2].error = "the judge exited with code 1"
    record.cells[2].result_path = "reports/campaigns/runs/demo/an-earlier-attempt.json"
    manager.restore(record)
    manager.restore(_stopped(["done"] * 4, identifier="a-later-campaign"))
    reopen(manager, record, resume_plan(record))

    assert manager.current is record and manager.busy, "it holds the lab while it runs"
    assert manager.history(kind="campaign")[0] is record
    assert (record.id, record.campaign_id, record.result_dir, record.request.seed_campaign) == (
        "20260925T151547Z-demo", "demo-20260925T151547Z", "reports/campaigns/runs/demo", 4821)
    assert (record.state, record.error, record.finished_at) == (RunState.VALIDATING, None, None)
    assert [cell.state for cell in record.cells] == ["done", "done", "pending", "pending"]
    assert (record.cells[2].error, record.cells[2].result_path) == (None, None)
    # A page opened now must not stop reading where the first run ended.
    assert all(event["type"] != "finished" for event in record.events)
    assert [event["cell"]["state"] for event in list(record.events)[-2:]] == ["pending", "pending"]


def test_the_runner_walks_only_the_cells_it_runs_again(tmp_path, monkeypatch):
    record = _stopped(["done", "done", "failed", "skipped"], result_dir=str(tmp_path / "demo"))
    compiled = []

    def refused(request, cells, **kwargs):
        """Stands in for the compile step, so the campaign ends before any lab is touched."""
        compiled.extend(cells)
        raise ValueError("no lab here")

    monkeypatch.setattr(campaign_runner, "preflight", refused)
    manager = RunManager()
    manager.restore(record)
    cells = resume_plan(record)
    reopen(manager, record, cells)
    config = replace(WebUIConfig(), export_dir=str(tmp_path / "out"))
    asyncio.run(campaign_runner.run_campaign(manager, config, record, rerun=cells))

    assert [cell.index for cell in compiled] == [2, 3]
    narration = (tmp_path / "demo" / "launcher.log").read_text(encoding="utf-8").splitlines()
    assert narration[0] == ("campaign demo-20260925T151547Z: resuming 2 of 4 cells, "
                            "on the seeds they were planned with")
    # The cells done keep their outcome; the ones not reached this time are skipped again.
    assert [cell.state for cell in record.cells] == ["done", "done", "skipped", "skipped"]
    assert record.state is RunState.FAILED and not manager.busy


def test_a_campaign_read_back_after_a_restart_resumes_at_its_address(tmp_path, monkeypatch):
    record = _stopped(["done", "done", "failed", "skipped"], result_dir=str(tmp_path / "demo"))
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "campaign.json").write_text(json.dumps(_manifest(record)), encoding="utf-8")
    moved = replace(routes.config, campaign_dir=str(tmp_path), log_dir=str(tmp_path / "episodes"))
    fresh = RunManager()
    for module in (routes, app_module):
        monkeypatch.setattr(module, "config", moved)
        monkeypatch.setattr(module, "MANAGER", fresh)
    handed = {}

    async def runner(manager, config, record, *, rerun=None):
        """Stands in for run_campaign, which would deploy the lab."""
        handed.update(record=record, rerun=rerun)

    monkeypatch.setattr(routes, "run_campaign", runner)
    with TestClient(app_module.app) as client:
        response = client.post(f"/api/campaigns/{record.id}/resume")
        assert response.status_code == 201, response.text
        assert response.json() == {"id": record.id, "campaign_id": record.campaign_id,
                                   "seed_campaign": 4821, "seeds": [9, 17], "cells": 2}
        assert handed["record"] is fresh.get(record.id)
        assert [cell.index for cell in handed["rerun"]] == [2, 3]
        assert fresh.busy
        again = client.post(f"/api/campaigns/{record.id}/resume")
        assert again.status_code == 409 and "in flight" in again.json()["detail"]
    fresh.finish(handed["record"], error="ended by the test")


def test_a_resume_is_refused_by_name(monkeypatch):
    fresh = RunManager()
    monkeypatch.setattr(routes, "MANAGER", fresh)
    fresh.restore(_stopped(["done"] * 4, identifier="all-done"))
    client = TestClient(app_module.app)
    assert client.post("/api/campaigns/nowhere/resume").status_code == 404
    refused = client.post("/api/campaigns/all-done/resume")
    assert refused.status_code == 409 and "nothing to resume" in refused.json()["detail"]
    assert not fresh.busy


def test_a_record_left_by_an_earlier_attempt_is_not_read_as_this_one(tmp_path):
    (tmp_path / "cell-seed9-run1.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    earlier = frozenset(results_of(tmp_path, "cell-seed9"))
    said: list[str] = []
    assert collect(tmp_path, tmp_path, "cell-seed9", said.append, earlier=earlier)["result_path"] is None
    assert said == ["no result file was written for this episode"]
    (tmp_path / "cell-seed9-run2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    found = collect(tmp_path, tmp_path, "cell-seed9", said.append, earlier=earlier)
    assert found["result_path"].endswith("cell-seed9-run2.json")
