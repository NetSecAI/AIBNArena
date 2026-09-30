"""A campaign the web interface ran is laid out one folder per model, campaign, subject and intent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.tests.test_generate_campaign_report import MODEL, PLAN, RAG, _load, build_campaign

export_results = _load("export_results")


def _campaign(root: Path, plan=PLAN) -> Path:
    """The fixture campaign, its records worded `high`, with the judge's output per cell."""
    campaign = build_campaign(root, plan)
    manifest = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    (campaign / "judge-logs").mkdir()
    for cell in manifest["cells"]:
        if not cell["result_path"]:
            continue
        path = Path(cell["result_path"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document["provenance"]["intent_variant"] = "high"
        path.write_text(json.dumps(document), encoding="utf-8")
        (campaign / "judge-logs" / f"{cell['experiment_id']}.log").write_text(
            f"judge of {cell['experiment_id']}\n", encoding="utf-8")
    return campaign


def test_each_subject_is_one_cell_under_the_model_and_the_campaign(tmp_path):
    campaign = _campaign(tmp_path / "rag-vs-baseline")
    infos = export_results.export_campaign(tmp_path / "out", campaign)
    base = tmp_path / "out" / "fr-gpt-5.4" / "rag-vs-baseline"
    assert [Path(info["path"]) for info in infos] == [base / "langchain" / "high", base / "langchain_rag" / "high"]
    for cell in (base / "langchain" / "high", base / "langchain_rag" / "high"):
        assert sorted(path.name for path in (cell / "records").iterdir()) == [
            "connectivity.disable_interface.m1.json", "connectivity.remove_ip.m1.json"]
        for name in ("index.md", "reports/summary.txt", "reports/summary.json",
                     "reports/evaluation_parameters.json", "reports/cell.json",
                     "reports/tasks/connectivity.remove_ip.m1/report.html",
                     "reports/tasks/connectivity.remove_ip.m1/summary.md"):
            assert (cell / name).is_file(), name
    # The whole-cell tables read this cell's records only, not the campaign's other subject.
    summary = json.loads((base / "langchain_rag" / "high" / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["totals"]["results"] == 2 and {row["subject"] for row in summary["cells"]} == {RAG}
    judge = base / "langchain" / "high" / "logs" / "judge" / "connectivity.remove_ip.m1.judge.log"
    assert judge.read_text(encoding="utf-8").startswith("judge of langchain_agent-connectivity-remove_ip-m1")


def test_a_named_experiment_replaces_the_campaign_id(tmp_path):
    campaign = _campaign(tmp_path / "rag-vs-baseline")
    export_results.export_campaign(tmp_path / "out", campaign, experiment="e6_rag")
    assert (tmp_path / "out" / "fr-gpt-5.4" / "e6_rag" / "langchain_rag" / "high" / "index.md").is_file()


def test_a_plate_run_on_two_seeds_keeps_both_episodes(tmp_path):
    plan = (("langchain_agent", "LangChain ANI Baseline", "connectivity.remove_ip.m1", 11, True),
            ("langchain_agent", "LangChain ANI Baseline", "connectivity.remove_ip.m1", 22, False))
    campaign = _campaign(tmp_path / "seeds", plan)
    [info] = export_results.export_campaign(tmp_path / "out", campaign)
    assert sorted(path.name for path in (Path(info["path"]) / "records").iterdir()) == [
        "connectivity.remove_ip.m1.seed11.json", "connectivity.remove_ip.m1.seed22.json"]


def test_a_record_of_another_model_is_not_filed_under_the_cells(tmp_path):
    campaign = _campaign(tmp_path / "mismatch")
    manifest = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    wrong = Path(manifest["cells"][0]["result_path"])
    document = json.loads(wrong.read_text(encoding="utf-8"))
    document["provenance"]["sut_reported_model"] = "openai/other"
    wrong.write_text(json.dumps(document), encoding="utf-8")
    infos = export_results.export_campaign(tmp_path / "out", campaign)
    assert {info["subject"]: info["records"] for info in infos} == {"langchain": 1, "langchain_rag": 2}


def test_the_command_line_takes_a_campaign_and_refuses_a_cell_without_its_model(tmp_path, capsys):
    campaign = _campaign(tmp_path / "rag-vs-baseline")
    assert export_results.main(["--out", str(tmp_path / "out"), "--campaign", str(campaign)]) == 0
    assert "langchain_rag/high: 2 records" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        export_results.main(["--out", str(tmp_path / "out"), "--cell", "langchain", "high", str(campaign)])


def test_the_folder_names_drop_the_provider_and_the_agent_suffix():
    assert export_results.model_folder(MODEL) == "fr-gpt-5.4"
    assert export_results.model_folder("openai/qwen35-9b-think") == "qwen35-9b-think"
    assert export_results.subject_folder("langchain_agent") == "langchain"
    assert export_results.subject_folder("langchain_rag_agent") == "langchain_rag"
