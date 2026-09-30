"""The diagnosis verdict on its way into the report and back into old records.

`generate_report.py` shows `metrics.diagnosis` as a section a reader can scan;
`score_diagnoses.py` gives a record from before the step the same block, from the
same replay `evaluation_parameters.py` binds the faulty device with.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for _path in (str(REPO), str(REPO / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from scripts.generate_report import diagnosis_html, run_summary  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BLOCK = {
    "applicable": True, "reference": "On leaf1, IPv4 forwarding is disabled.",
    "method": "router_ipv4_forwarding_disabled", "operation": "set_ipv4_forwarding", "target": "leaf1",
    "hypothesis": "leaf1 stopped routing between its interfaces", "hypothesis_source": "final_response",
    "found": True, "score": 3.21, "log_p_yes": -0.04, "log_p_no": -3.25,
    "judge": {"model": "ministral-3-8b-instruct", "api_base": "http://127.0.0.1:18002/v1",
              "prompt_id": "fault-pluie-v2", "method": "prompt_logprobs"},
    "reason": None,
}


def test_the_run_summary_carries_the_block_and_the_html_shows_the_verdict():
    summary = run_summary({"metrics": {"success": False, "diagnosis": BLOCK}})
    assert summary["diagnosis"] == BLOCK
    assert run_summary({"metrics": {"success": False}})["diagnosis"] is None

    html = diagnosis_html(BLOCK)
    assert "found the problem" in html and "+3.210" in html
    assert "On leaf1, IPv4 forwarding is disabled." in html
    assert "ministral-3-8b-instruct" in html and "fault-pluie-v2" in html
    assert diagnosis_html(None) == ""


def test_an_unjudged_block_shows_its_reason_not_a_verdict():
    html = diagnosis_html({**BLOCK, "found": None, "score": None, "judge": None,
                           "reason": "no diagnosis judge configured"})
    assert "no diagnosis judge configured" in html
    assert "found the problem" not in html.split("verdict")[1].split("</tr>")[0].replace("did not find", "")
    html = diagnosis_html({**BLOCK, "found": False, "score": -1.5})
    assert "did not find the problem" in html and "-1.500" in html


class FakeJudge:
    def identity(self):
        return {"model": "fake", "api_base": "http://fake/v1", "prompt_id": "fault-pluie-v2"}

    def score(self, reference, hypothesis):
        return {"score": 0.5, "log_p_yes": -0.5, "log_p_no": -1.0, "method": "prompt_logprobs"}


def _recorded_episode() -> tuple[Path, dict]:
    runs = Path("/home/jongmin/ibn-integration-test/runs")
    candidates = sorted(runs.glob("e8/**/results/*.json")) + sorted(runs.glob("20260906/**/results/*.json"))
    if not candidates:
        pytest.skip("no recorded campaign on this machine")
    path = candidates[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_a_record_from_before_the_step_is_scored_from_its_summary_and_written_back(tmp_path):
    scorer = _load("score_diagnoses")
    path, document = _recorded_episode()
    document.get("metrics", {}).pop("diagnosis", None)
    block = scorer.score_record(document, FakeJudge())
    if (block.get("reason") or "").startswith("the compiled instance could not be replayed"):
        pytest.skip("the record's topology digest does not match this checkout")
    assert block["reference"], "the injected fault is described from the replayed method"
    assert block["target"], "and names the device"
    summary = ((document.get("sut_result") or {}).get("final_response") or {}).get("summary")
    if summary:
        assert block["hypothesis_source"] == "summary" and block["found"] is True
    else:
        assert block["found"] is False and block["reason"] == "the subject stated no diagnosis"
    assert block["offline"]["script"] == "scripts/score_diagnoses.py"

    copy = tmp_path / path.name
    copy.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    scorer.write_back(copy, document, block)
    written = json.loads(copy.read_text(encoding="utf-8"))
    assert written["metrics"]["diagnosis"]["reference"] == block["reference"]
    assert not list(tmp_path.glob("*.tmp")), "the temporary file is replaced, not left behind"
