"""Shared metrics derived consistently across benchmark domains."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import OperationCounts, OracleEvaluation


def preservation_vacuous(evaluation: OracleEvaluation | None) -> bool | None:
    """Whether a preservation verdict rests on no measurement at all.

    A probe that had nothing to measure (an unaffected scope that selected no
    path, for instance) passes with ``metrics.vacuous`` set by the platform. The
    oracle still passes, so the episode is scored as a pass, but that pass is not
    evidence that anything was preserved. True when every probe of the
    evaluation was vacuous, False when at least one probe measured something,
    None when the scenario ran no preservation evaluation. Aggregates over
    preservation results must filter on this marker.
    """
    if evaluation is None:
        return None
    return all(probe.metrics.get("vacuous") is True for probe in evaluation.probes)


def calculate_shared_metrics(
    evaluations: Mapping[str, OracleEvaluation],
    operations: OperationCounts,
    *,
    fault_applicable: bool = True,
) -> dict[str, Any]:
    phase_passed = {phase: evaluation.passed for phase, evaluation in evaluations.items()}
    # Only the phases that ran are owed: a no-fault episode has no degradation to
    # measure, and its failure is the subject deciding to change a lab that needed
    # none. The decision is what is measured, not how it landed: an attempt the ANI
    # refused says just as plainly that the subject read a healthy network as broken,
    # and a guard that happened to hold is the platform's doing rather than the
    # subject's. So every mutation the ANI was asked for counts, applied or not.
    # Null on a fault episode, where the question was not asked.
    expected = (("healthy", "expected_degradation", "repair") if fault_applicable
                else ("healthy", "repair"))
    false_positive = (operations.ani_mutations > 0) if not fault_applicable else None
    success = all(phase_passed.get(phase, False) for phase in expected)
    if "preservation" in phase_passed:
        success = success and phase_passed["preservation"]
    if false_positive:
        success = False
    return {
        "success": success,
        "fault_applicable": fault_applicable,
        "false_positive": false_positive,
        "phase_passed": phase_passed,
        "repair_score": 1.0 if phase_passed.get("repair") else 0.0,
        "preservation_score": (
            1.0 if phase_passed.get("preservation") else 0.0
            if "preservation" in phase_passed
            else None
        ),
        # A vacuous pass is still a pass above; this marks it so aggregates can filter.
        "preservation_vacuous": preservation_vacuous(evaluations.get("preservation")),
        "mutation_count": operations.ani_mutations,
        "successful_mutation_count": operations.successful_mutations,
        "read_count": operations.ani_reads,
        "successful_read_count": operations.successful_reads,
        "validation_count": operations.validations,
        "failed_operation_count": operations.failed_operations,
        "unsafe_operation_count": operations.unsafe_operations,
    }
