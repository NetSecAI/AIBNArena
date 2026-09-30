"""Campaigns read back after a restart: the table, the page and its logs."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import webui.app as app_module
from scripts.tests.test_generate_campaign_report import build_campaign
from webui import routes
from webui.campaign import CampaignRequest, ScenarioSelection, plan
from webui.config import WebUIConfig
from webui.orchestrator import CampaignRecord, CellRecord, RunManager, RunState, campaign_runner, history
from webui.orchestrator.campaign_runner import _manifest
from webui.tests.test_campaign_report import _record


def _restored(root: Path) -> RunManager:
    manager = RunManager()
    history.restore_campaigns(manager, root)
    return manager


def _campaign(root: Path, **fields) -> Path:
    """The fixture campaign, its campaign.json as the runner writes it once it has ended."""
    campaign = build_campaign(root)
    manifest = {**_manifest(_record(campaign)), "state": "done", **fields}
    # Written before the record named the page's id, as the first campaigns were.
    del manifest["id"]
    (campaign / "campaign.json").write_text(json.dumps(manifest), encoding="utf-8")
    return campaign


def _rewrite(campaign: Path, change) -> None:
    path = campaign / "campaign.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_a_finished_campaign_is_read_back_as_it_ended(tmp_path):
    campaign = _campaign(tmp_path / "rag-vs-baseline",
                         report_path="reports/campaigns/runs/rag-vs-baseline/reports/campaign.report.html",
                         export_paths=["reports/campaigns/fr-gpt-5.4/rag-vs-baseline/langchain/high"])
    manager = _restored(tmp_path)
    # Written before the record named the page's id: reached by its campaign id.
    record = manager.get("rag-vs-baseline")
    assert isinstance(record, CampaignRecord) and record.state is RunState.DONE
    assert [cell.state for cell in record.cells] == ["done", "done", "done", "done", "skipped"]
    assert record.cells[0].verdict == {"benchmark_success": True}
    assert record.cells[0].cell["experiment_id"].startswith("langchain_agent-connectivity-disable_interface-m1")
    assert record.report_path.endswith("campaign.report.html")
    assert record.export_paths == ["reports/campaigns/fr-gpt-5.4/rag-vs-baseline/langchain/high"]
    assert record.result_dir == str(campaign)
    # Nothing is running it, so it cannot hold the lab.
    assert not manager.busy
    assert record.events[-1] == {"type": "finished", **record.summary()}


def test_a_campaign_is_restored_at_the_address_its_page_had(tmp_path):
    campaign = _campaign(tmp_path / "named")
    _rewrite(campaign, lambda manifest: manifest.update(id="20260924T120000Z-abcd"))
    assert _restored(tmp_path).get("20260924T120000Z-abcd") is not None


def test_a_campaign_the_server_stopped_midway_reads_back_as_failed(tmp_path):
    campaign = _campaign(tmp_path / "stopped", state="running_benchmark")

    def interrupted(manifest):
        manifest["cells"][3].update(state="running", verdict=None, result_path=None)
        manifest["cells"][4].update(state="pending", error=None)

    _rewrite(campaign, interrupted)
    record = _restored(tmp_path).get("stopped")
    assert record.state is RunState.FAILED
    assert record.error == "the web server stopped before this campaign finished"
    assert (record.cells[3].state, record.cells[4].state) == ("failed", "skipped")
    assert record.cells[3].error.startswith("interrupted")


def test_the_logs_are_read_back_on_first_view_in_the_matrix_order(tmp_path):
    campaign = _campaign(tmp_path / "logs")
    record = _restored(tmp_path).get("logs")
    first, second = (cell.cell["experiment_id"] for cell in record.cells[:2])
    (campaign / "launcher.log").write_text("campaign logs: 5 cells\n", encoding="utf-8")
    (campaign / "judge-logs").mkdir()
    (campaign / "server-logs").mkdir()
    (campaign / "judge-logs" / f"{second}.log").write_text("judge two\n", encoding="utf-8")
    (campaign / "judge-logs" / f"{first}.log").write_text("judge one\n", encoding="utf-8")
    (campaign / "server-logs" / f"{first}.log").write_text("server started\n", encoding="utf-8")
    history.load_logs(record)
    lines = [(event["source"], event["line"]) for event in record.events if event["type"] == "log"]
    assert lines == [("launcher", "campaign logs: 5 cells"), ("benchmark", "judge one"),
                     ("benchmark", "judge two"), ("sut", "server started")]
    assert record.events[-1]["type"] == "finished"
    history.load_logs(record)
    assert len([event for event in record.events if event["type"] == "log"]) == 4, "read once"


def test_a_record_this_version_cannot_read_is_skipped(tmp_path):
    _campaign(tmp_path / "good")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "campaign.json").write_text("{", encoding="utf-8")
    assert history.restore_campaigns(RunManager(), tmp_path) == 1


def test_a_restarted_server_lists_the_campaign_and_serves_its_page(tmp_path, monkeypatch):
    campaign = _campaign(tmp_path / "listed")
    (campaign / "launcher.log").write_text("campaign listed: 5 cells\n", encoding="utf-8")
    moved = replace(routes.config, campaign_dir=str(tmp_path), log_dir=str(tmp_path / "episodes"))
    fresh = RunManager()
    for module in (routes, app_module):
        monkeypatch.setattr(module, "config", moved)
        monkeypatch.setattr(module, "MANAGER", fresh)
    with TestClient(app_module.app) as client:
        assert '<a href="/campaign/listed">listed</a>' in client.get("/campaigns").text
        assert client.get("/campaign/listed").status_code == 200
        stream = client.get("/api/campaigns/listed/events").text
        assert "campaign listed: 5 cells" in stream
        assert '"type": "finished"' in stream


def test_the_runner_leaves_its_narration_and_its_page_id_on_disk(tmp_path, monkeypatch):
    request = CampaignRequest(
        architectures=["langchain_agent"], models=["openai/fr-gpt-5.4"],
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1", seed=11)],
        execution_budget_seconds=400)
    cells = plan(request)
    record = CampaignRecord(id="20260924T120000Z-abcd", request=request, campaign_id="narrated",
                            result_dir=str(tmp_path / "narrated"), plan=cells,
                            cells=[CellRecord(cell=cell.summary()) for cell in cells])

    def refused(*args, **kwargs):
        """Stands in for the compile step, so the campaign ends before any lab is touched."""
        raise ValueError("no lab here")

    monkeypatch.setattr(campaign_runner, "preflight", refused)
    manager = RunManager()
    manager.create(record)
    config = replace(WebUIConfig(), export_dir=str(tmp_path / "out"))
    asyncio.run(campaign_runner.run_campaign(manager, config, record))
    narration = (tmp_path / "narrated" / "launcher.log").read_text(encoding="utf-8").splitlines()
    assert narration[0] == "campaign narrated: 1 cells"
    assert "ValueError: no lab here" in narration
    restored = _restored(tmp_path).get(record.id)
    assert restored is not None and restored.state is RunState.FAILED
    assert [cell.state for cell in restored.cells] == ["skipped"]
