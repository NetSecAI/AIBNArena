"""The judge's command-line settings reach the lifecycle: the reading, the audit log
beside the reports, and the subject's provider as the judge's default endpoint."""
from __future__ import annotations

import argparse

import pytest

from benchmarks.core.contracts import ExperimentConfig
from benchmarks.run import diagnosis_judge_override, diagnosis_judge_settings


def _args(**values):
    base = {"diagnosis_judge_model": None, "diagnosis_judge_api_base": None,
            "diagnosis_judge_reading": None, "diagnosis_judge_audit_log": None}
    return argparse.Namespace(**{**base, **values})


def test_a_named_judge_takes_its_reading_and_the_subjects_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "subject-key")
    monkeypatch.delenv("IBN_DIAGNOSIS_JUDGE_API_KEY", raising=False)
    override = diagnosis_judge_override(_args(diagnosis_judge_model="gpt-4.1",
                                              diagnosis_judge_api_base="https://api.openai.com/v1",
                                              diagnosis_judge_reading="top_logprobs"))
    assert override == {"model": "gpt-4.1", "api_base": "https://api.openai.com/v1",
                        "api_key": "subject-key", "reading": "top_logprobs"}
    monkeypatch.setenv("IBN_DIAGNOSIS_JUDGE_API_KEY", "judge-key")
    assert diagnosis_judge_override(_args(diagnosis_judge_model="m", diagnosis_judge_api_base="http://h/v1"))["api_key"] == "judge-key"


def test_a_judge_without_any_endpoint_is_refused_and_no_judge_is_none():
    assert diagnosis_judge_override(_args()) is None
    with pytest.raises(SystemExit):
        diagnosis_judge_override(_args(diagnosis_judge_model="gpt-4.1"))


def test_the_audit_log_lands_beside_the_reports_unless_named():
    config = ExperimentConfig(experiment_id="x", scenario_path="s.toml", testbed={},
                              report_dir="/tmp/reports-x",
                              diagnosis_judge={"model": "gpt-4.1", "api_base": "https://api.openai.com/v1"})
    settings = diagnosis_judge_settings(config, _args(diagnosis_judge_reading="top_logprobs"))
    assert settings["audit_log"] == "/tmp/reports-x/diagnosis-audit.jsonl"
    assert settings["reading"] == "top_logprobs"
    named = diagnosis_judge_settings(config, _args(diagnosis_judge_audit_log="/tmp/mine.jsonl"))
    assert named["audit_log"] == "/tmp/mine.jsonl"
    assert diagnosis_judge_settings(ExperimentConfig(experiment_id="x", scenario_path="s", testbed={}), _args()) is None


def test_the_experiment_file_may_name_the_reading_and_the_audit_log():
    config = ExperimentConfig.from_mapping({
        "experiment_id": "x", "scenario_path": "s", "testbed": {},
        "diagnosis_judge": {"model": "gpt-4.1", "api_base": "https://api.openai.com/v1",
                            "reading": "top_logprobs", "audit_log": "logs/judge.jsonl", "unknown": 1},
    })
    assert config.diagnosis_judge == {"model": "gpt-4.1", "api_base": "https://api.openai.com/v1",
                                      "reading": "top_logprobs", "audit_log": "logs/judge.jsonl"}
