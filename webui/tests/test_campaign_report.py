"""A campaign ends with its own page, and the interface serves what the judge wrote."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.tests.test_generate_campaign_report import MODEL, build_campaign
from webui import routes
from webui.app import app
from webui.campaign import CampaignRequest, ScenarioSelection
from webui.orchestrator import CampaignRecord, CellRecord, RunManager
from webui.orchestrator.campaign_runner import _export, _write_report

CELL_FIELDS = ("index", "architecture", "model", "experiment", "scenario_id", "no_fault", "seed",
               "experiment_id")


def _record(campaign: Path) -> CampaignRecord:
    """The campaign as the runner holds it when its last cell has ended."""
    manifest = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    request = CampaignRequest(
        architectures=["langchain_agent", "langchain_rag_agent"], models=[MODEL],
        scenarios=[ScenarioSelection(experiment="connectivity-smoke",
                                     scenario_id="connectivity.disable_interface.m1", seed=11)],
        execution_budget_seconds=400)
    cells = [CellRecord(cell={field: cell[field] for field in CELL_FIELDS}, state=cell["state"],
                        verdict=cell["verdict"], result_path=cell["result_path"],
                        report_path=cell["report_path"], error=cell["error"])
             for cell in manifest["cells"]]
    return CampaignRecord(id="page", request=request, campaign_id=campaign.name,
                          result_dir=str(campaign), cells=cells)


def test_a_campaign_ends_with_its_page_and_its_record_names_it(tmp_path):
    campaign = build_campaign(tmp_path / "rag-vs-baseline")
    record = _record(campaign)
    said: list[str] = []
    _write_report(record, campaign, None, said.append)
    page = campaign / "reports" / "campaign.report.html"
    assert page.is_file()
    # Outside the repository the path stays absolute; inside it, it is repository-relative.
    assert record.report_path == str(page)
    assert record.summary()["report_path"] == record.report_path, "the finished event carries it"
    assert said == [f"campaign report: {record.report_path}"]


def test_the_page_says_why_a_campaign_stopped(tmp_path):
    campaign = build_campaign(tmp_path / "stopped")
    _write_report(_record(campaign), campaign, "campaign stopped: model mismatch", lambda line: None)
    assert "campaign stopped: model mismatch" in (
        campaign / "reports" / "campaign.report.html").read_text(encoding="utf-8")


def test_a_page_that_cannot_be_built_is_said_and_fails_nothing(tmp_path):
    record = _record(build_campaign(tmp_path / "campaign"))
    said: list[str] = []
    _write_report(record, tmp_path / "gone", None, said.append)
    assert record.report_path is None
    assert said and said[0].startswith("campaign report: not written")


def test_a_campaign_lays_its_records_out_per_subject_and_its_record_names_them(tmp_path):
    campaign = build_campaign(tmp_path / "rag-vs-baseline")
    record = _record(campaign)
    said: list[str] = []
    _export(record, campaign, tmp_path / "out", said.append)
    base = tmp_path / "out" / "fr-gpt-5.4" / "rag-vs-baseline"
    # The fixture's records name no intent wording, which the layout says rather than guesses.
    cells = [base / "langchain" / "unrecorded", base / "langchain_rag" / "unrecorded"]
    assert record.export_paths == [str(cell) for cell in cells]
    assert all((cell / "index.md").is_file() and (cell / "records").is_dir() for cell in cells)
    assert record.summary()["export_paths"] == record.export_paths, "the finished event carries them"
    assert said == [f"records laid out: {cell}/" for cell in cells]


def test_a_layout_that_cannot_be_written_is_said_and_fails_nothing(tmp_path):
    record = _record(build_campaign(tmp_path / "campaign"))
    said: list[str] = []
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder would go", encoding="utf-8")
    _export(record, tmp_path / "campaign", blocked, said.append)
    assert record.export_paths == []
    assert said and said[0].startswith("records laid out: not written")


def test_the_page_charts_each_subjects_parameters_from_the_report(tmp_path, monkeypatch):
    campaign = build_campaign(tmp_path / "charted")
    record = _record(campaign)
    manager = RunManager()
    manager.create(record)
    monkeypatch.setattr(routes, "MANAGER", manager)
    client = TestClient(app)
    assert client.get(f"/api/campaigns/{record.id}/parameters").status_code == 404, "no report yet"
    _write_report(record, campaign, None, lambda line: None)
    body = client.get(f"/api/campaigns/{record.id}/parameters").json()
    labels = [contender["label"] for contender in body["contenders"]]
    assert len(labels) == 2 and all(label.endswith("fr-gpt-5.4") for label in labels)
    for contender in body["contenders"]:
        parameters = body["parameters"][contender["key"]]
        assert parameters["pass_rate"]["denominator"] == 2
        assert set(parameters["ani_call_type_ratio"]) >= {"check_config", "apply_config", "validate"}


def test_the_interface_serves_the_reports_and_nothing_outside_them():
    client = TestClient(app)
    assert client.get("/reports/README.md").status_code == 200
    assert client.get("/reports/..%2F.env").status_code == 404
    assert client.get("/reports/%2E%2E/webui/models.json").status_code == 404
