"""Finding what an episode wrote, and reading the verdict out of it.

One episode and one cell of a campaign write the same record in the same
format, so they read it the same way. The model provenance is part of that
reading: a record is a measurement of a named model, and a record that names
three different ones has measured something nobody asked for.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Collection

from webui.config import REPOSITORY


def results_of(directory: Path, experiment_id: str) -> list[Path]:
    """Every result written under this id: `{experiment_id}-{run}.json`, one per attempt."""
    return list(directory.glob(f"{experiment_id}-*.json"))


def newest_result(directory: Path, experiment_id: str, *,
                  earlier: Collection[Path] = ()) -> Path | None:
    """The result this episode wrote, newest first, leaving out the `earlier` ones.

    A cell run again when its campaign is resumed finds its earlier attempt's
    record beside it, and an attempt that wrote nothing must not be read as
    having written that one. Each attempt writes under its own run id, so the
    results present before it started are exactly the ones that are not its own.
    """
    matches = sorted((path for path in results_of(directory, experiment_id) if path not in earlier),
                     key=lambda path: path.stat().st_mtime)
    return matches[-1] if matches else None


def verdict_from_result(payload: dict[str, Any]) -> dict[str, Any]:
    """The fields `benchmarks/run.py` prints when an episode ends, plus who answered."""
    sut_result = payload.get("sut_result") or {}
    metrics = payload.get("metrics") or {}
    provenance = payload.get("provenance") or {}
    diagnosis = metrics.get("diagnosis") if isinstance(metrics.get("diagnosis"), dict) else {}
    return {
        "benchmark_success": metrics.get("success"),
        "environment_success": metrics.get("environment_success"),
        "converged": metrics.get("converged"),
        "lifecycle_status": str(payload.get("status", "unknown")).upper(),
        "sut_status": str(sut_result.get("status", "unknown")).upper(),
        "sut_verified": sut_result.get("verified"),
        "scenario": (payload.get("scenario") or {}).get("id"),
        "seed": provenance.get("scenario_seed"),
        "configured_model": provenance.get("configured_model"),
        "sut_reported_model": provenance.get("sut_reported_model"),
        "provider_reported_model": provenance.get("provider_reported_model"),
        # Evaluation parameter 13 on this episode: whether the subject's stated
        # diagnosis names the injected fault, the judge's margin for it, the
        # statement itself, and which model judged. None where the question was
        # not asked; the record's `metrics.diagnosis.reason` says why.
        "root_cause_identified": diagnosis.get("found"),
        "diagnosis_score": diagnosis.get("score"),
        "sut_diagnosis": diagnosis.get("hypothesis"),
        "diagnosis_judge": (diagnosis.get("judge") or {}).get("model")
        if isinstance(diagnosis.get("judge"), dict) else None,
    }


def provenance_mismatch(verdict: dict[str, Any], model: str) -> str | None:
    """What contradicts the model this episode was asked to measure, if anything.

    The provider's own name for the model is checked only when it gave one: a
    proxy that does not report a model is silent, not wrong. The other two are
    the subject's own word and this tool's, and either disagreeing means the
    record belongs to a model that was not the one under test.
    """
    mismatches = []
    if verdict.get("configured_model") != model:
        mismatches.append(f"configured_model: expected {model!r}, recorded {verdict.get('configured_model')!r}")
    # The subject's word is held to the model only when it gave one. An episode the
    # judge failed before the subject ran (a lab that would not deploy or reset)
    # records no subject at all; that is a failed cell, not a record of another
    # model, and it must not stop the campaign the way a real disagreement does.
    reported = verdict.get("sut_reported_model")
    if reported is not None and reported != model:
        mismatches.append(f"sut_reported_model: expected {model!r}, recorded {reported!r}")
    provider = verdict.get("provider_reported_model")
    if provider is not None and provider != model:
        mismatches.append(f"provider_reported_model: expected {model!r}, recorded {provider!r}")
    return "; ".join(mismatches) or None


def collect(
    result_dir: Path,
    report_dir: Path,
    experiment_id: str,
    sink: Callable[[str], None],
    *,
    earlier: Collection[Path] = (),
) -> dict[str, Any]:
    """The record this episode wrote, its verdict, and the report beside it.

    Returns empty fields rather than raising when nothing was written: an
    episode that failed before the judge could write is a reportable outcome,
    not a failure of reading. `earlier` are the results already there before
    this attempt started.
    """
    found: dict[str, Any] = {"result_path": None, "verdict": None, "report_path": None}
    result = newest_result(result_dir, experiment_id, earlier=earlier)
    if result is None:
        sink("no result file was written for this episode")
        return found
    found["result_path"] = repository_relative(result)
    found["verdict"] = verdict_from_result(json.loads(result.read_text(encoding="utf-8")))
    sink(f"result: {found['result_path']}")
    report = report_dir / f"{result.stem}.report.html"
    if report.is_file():
        found["report_path"] = repository_relative(report)
        sink(f"report: {found['report_path']}")
    return found


def repository_relative(path: Path) -> str:
    """Repository-relative when it can be, absolute when the run writes elsewhere."""
    try:
        return str(path.relative_to(REPOSITORY))
    except ValueError:
        return str(path)
