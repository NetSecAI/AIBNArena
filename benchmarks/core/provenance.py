"""Reproducibility metadata collected for every benchmark result."""
from __future__ import annotations

import hashlib
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from .contracts import ExperimentConfig, ScenarioDefinition, SUTResponse


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def git_state(repository: str | Path) -> dict[str, Any]:
    root = Path(repository)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Tracked changes decide dirtiness. An untracked file cannot alter what the
    # commit ran, and a checkout that always carries a few (local tooling, run
    # files) made every result "dirty" and the flag said nothing. Both counts are
    # recorded so a reader can still see what the working tree held.
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [line for line in status.splitlines() if line.strip()]
    untracked = sum(1 for line in lines if line.startswith("??"))
    modified = len(lines) - untracked
    return {"commit": commit, "dirty": modified > 0,
            "modified_files": modified, "untracked_files": untracked}


def collect_provenance(
    config: ExperimentConfig,
    scenario: ScenarioDefinition,
    oracles: Mapping[str, Mapping[str, Any]],
    response: SUTResponse,
    *,
    repository: str | Path,
) -> dict[str, Any]:
    return {
        # What the judge was told the campaign runs, or None when it was not
        # told; never a copy of what the subject reports, which is the value
        # this one is compared against.
        "configured_model": config.configured_model,
        "sut_reported_model": response.reported_model,
        "provider_reported_model": response.provider_model,
        "a2a_sdk_version": package_version("a2a-sdk"),
        "sut_identity": response.identity,
        "sut_version": response.version,
        "model_parameters": dict(response.runtime_parameters),
        # What served the model. A wall-clock budget is only comparable between
        # episodes that ran on comparable hardware, and without this the artifact
        # cannot tell whether a budget-exhausted episode hit the model's limit or
        # the device's throughput.
        "serving": dict(response.serving),
        "git": git_state(repository),
        "scenario_version": scenario.version,
        "scenario_seed": scenario.seed,
        # The campaign this episode belongs to. The scenario seed identifies the
        # episode; this identifies the campaign it was one cell of, and every
        # record of that campaign carries the same one.
        "seed_campaign": config.seed_campaign,
        "intent_variant": scenario.intent_variant,
        "oracle_versions": {
            phase: str(document["version"]) for phase, document in oracles.items()
        },
        "topology_sha256": file_sha256(scenario.topology),
        "reference_state_sha256": file_sha256(scenario.reference_state),
    }
