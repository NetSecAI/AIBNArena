"""Provenance says what the judge knew, not what the subject told it."""
from __future__ import annotations

import subprocess
from pathlib import Path

from benchmarks.core.contracts import ExperimentConfig, SUTResponse
from benchmarks.core.provenance import collect_provenance, git_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.org", "-c", "user.name=t",
                    "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def test_an_untracked_file_does_not_make_the_tree_dirty(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "local_tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    state = git_state(root)
    assert state["dirty"] is False
    assert state["modified_files"] == 0 and state["untracked_files"] == 1
    assert len(state["commit"]) == 40


def test_a_modified_tracked_file_does(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("two\n", encoding="utf-8")
    (root / "local_tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    state = git_state(root)
    assert state["dirty"] is True
    assert state["modified_files"] == 1 and state["untracked_files"] == 1


def _config(**overrides) -> ExperimentConfig:
    return ExperimentConfig(experiment_id="e", scenario_path="s.yaml", testbed={"platform": "fake"},
                            **overrides)


def _provenance(config: ExperimentConfig, tmp_path: Path) -> dict:
    root = _repo(tmp_path)
    topology = tmp_path / "topology.yaml"
    topology.write_text("topology: {}\n", encoding="utf-8")
    from types import SimpleNamespace
    scenario = SimpleNamespace(version="1.0.0", seed=0, intent_variant=None,
                               topology=str(topology), reference_state=str(topology))
    response = SUTResponse(result={}, identity="sut", version="1.0.0",
                           reported_model="qwen/served", provider_model="qwen/served-0.1")
    return collect_provenance(config, scenario, {"healthy": {"version": "1.0.0"}}, response,
                              repository=root)


def test_configured_model_is_what_the_judge_was_told(tmp_path: Path) -> None:
    told = _provenance(_config(configured_model="qwen/intended"), tmp_path)
    assert told["configured_model"] == "qwen/intended"
    assert told["sut_reported_model"] == "qwen/served"


def test_configured_model_is_null_when_the_judge_was_not_told(tmp_path: Path) -> None:
    untold = _provenance(_config(), tmp_path)
    assert untold["configured_model"] is None
    assert untold["sut_reported_model"] == "qwen/served"


def test_experiment_config_reads_the_model_from_its_mapping() -> None:
    base = {"experiment_id": "e", "scenario_path": "s", "testbed": {}}
    assert ExperimentConfig.from_mapping(base).configured_model is None
    assert ExperimentConfig.from_mapping({**base, "configured_model": "m/x"}).configured_model == "m/x"
    assert ExperimentConfig.from_mapping({**base, "configured_model": ""}).configured_model is None
