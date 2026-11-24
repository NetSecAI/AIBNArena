from __future__ import annotations

import unittest
from typing import Any, Mapping

from benchmarks.core import BenchmarkLifecycle
from benchmarks.core.contracts import OperationCounts, OracleEvaluation, ProbeEvaluation
from benchmarks.core.metrics import calculate_shared_metrics, preservation_vacuous, repair_without_change
from benchmarks.core.reporting import repair_without_change_of, validate_result


# The shape the containerlab platform returns for an unaffected scope that
# selected no path: nothing was pinged, and the record says so.
VACUOUS_PROBE_METRICS: Mapping[str, Any] = {
    "success": True,
    "total_checks": 0,
    "passed_checks": 0,
    "failed_checks": 0,
    "checks": [],
    "vacuous": True,
    "reason": "scope unaffected selected no paths: every required path touches an affected node ['app1', 'web1']",
}
MEASURED_PROBE_METRICS: Mapping[str, Any] = {
    "success": True,
    "total_checks": 6,
    "passed_checks": 6,
    "failed_checks": 0,
    "checks": [],
}


def probe(probe_id: str, metrics: Mapping[str, Any]) -> ProbeEvaluation:
    return ProbeEvaluation(
        probe_id=probe_id,
        passed=True,
        metrics=dict(metrics),
        thresholds=({"metric": "failed_checks", "operator": "lte", "value": 0},),
        threshold_results=(True,),
    )


def evaluation(phase: str, *probes: ProbeEvaluation) -> OracleEvaluation:
    return OracleEvaluation(
        oracle_id=f"connectivity.test.{phase}",
        version="1.0.0",
        phase=phase,
        passed=True,
        probes=tuple(probes),
    )


def passing_phases() -> dict[str, OracleEvaluation]:
    return {
        phase: evaluation(phase, probe("path", MEASURED_PROBE_METRICS))
        for phase in ("healthy", "expected_degradation", "repair")
    }


class PreservationVacuousMarkerTests(unittest.TestCase):
    def test_every_probe_vacuous_marks_the_pass_and_keeps_it_a_pass(self) -> None:
        evaluations = passing_phases()
        evaluations["preservation"] = evaluation(
            "preservation", probe("unaffected_paths", VACUOUS_PROBE_METRICS)
        )

        metrics = calculate_shared_metrics(evaluations, OperationCounts())

        self.assertIs(metrics["preservation_vacuous"], True)
        self.assertEqual(1.0, metrics["preservation_score"])
        self.assertTrue(metrics["phase_passed"]["preservation"])
        self.assertTrue(metrics["success"])

    def test_a_measured_probe_is_not_vacuous(self) -> None:
        evaluations = passing_phases()
        evaluations["preservation"] = evaluation(
            "preservation", probe("unaffected_paths", MEASURED_PROBE_METRICS)
        )

        metrics = calculate_shared_metrics(evaluations, OperationCounts())

        self.assertIs(metrics["preservation_vacuous"], False)
        self.assertEqual(1.0, metrics["preservation_score"])
        self.assertTrue(metrics["success"])

    def test_one_measured_probe_among_vacuous_ones_is_not_vacuous(self) -> None:
        evaluations = passing_phases()
        evaluations["preservation"] = evaluation(
            "preservation",
            probe("unaffected_paths", VACUOUS_PROBE_METRICS),
            probe("services", MEASURED_PROBE_METRICS),
        )

        metrics = calculate_shared_metrics(evaluations, OperationCounts())

        self.assertIs(metrics["preservation_vacuous"], False)

    def test_no_preservation_evaluation_reports_none(self) -> None:
        metrics = calculate_shared_metrics(passing_phases(), OperationCounts())

        self.assertIsNone(metrics["preservation_vacuous"])
        self.assertIsNone(metrics["preservation_score"])
        self.assertTrue(metrics["success"])

    def test_helper_states(self) -> None:
        self.assertIsNone(preservation_vacuous(None))
        self.assertIs(
            preservation_vacuous(evaluation("preservation", probe("a", VACUOUS_PROBE_METRICS))),
            True,
        )
        self.assertIs(
            preservation_vacuous(evaluation("preservation", probe("a", MEASURED_PROBE_METRICS))),
            False,
        )


def result_payload(metrics: Mapping[str, Any]) -> dict[str, Any]:
    phases = ("healthy", "expected_degradation", "repair", "preservation")
    return {
        "schema_version": "1.0",
        "run_id": "run-vacuous",
        "created_at": "2026-09-04T00:00:00+00:00",
        "status": "completed",
        "experiment_id": "exp",
        "scenario": {
            "id": "connectivity.test",
            "version": "1.0.0",
            "domain": "connectivity",
            "sut_task_id": "sut-" + "0" * 32,
        },
        "provenance": {
            "configured_model": None,
            "sut_reported_model": None,
            "provider_reported_model": None,
            "a2a_sdk_version": None,
            "sut_identity": "sut",
            "sut_version": "1.0.0",
            "model_parameters": {},
            "git": {"commit": "0" * 40, "dirty": False},
            "scenario_version": "1.0.0",
            "oracle_versions": {phase: "1.0.0" for phase in phases},
            "topology_sha256": "0" * 64,
            "reference_state_sha256": "0" * 64,
            "scenario_seed": 0,
        },
        "operation_counts": {
            **{
                name: 0
                for name in (
                    "llm_calls", "ani_reads", "ani_mutations", "validations",
                    "successful_mutations", "failed_operations",
                    "unsafe_operations",
                )
            },
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        "phase_durations": {phase: 0.0 for phase in BenchmarkLifecycle.PHASES},
        "evaluations": {phase: {} for phase in phases},
        "metrics": dict(metrics),
        "sut_result": {},
        "cleanup_result": {},
        "convergence_result": {},
        "error": None,
    }


class ResultSchemaTests(unittest.TestCase):
    def test_metrics_carrying_the_vacuous_markers_validate(self) -> None:
        evaluations = passing_phases()
        evaluations["preservation"] = evaluation(
            "preservation", probe("unaffected_paths", VACUOUS_PROBE_METRICS)
        )
        metrics = {
            **calculate_shared_metrics(evaluations, OperationCounts()),
            "non_regression_vacuous": True,
        }
        self.assertIn("preservation_vacuous", metrics)

        payload = result_payload(metrics)
        validate_result(payload)

        # The same payload is rejected once it strays from the schema, so the
        # acceptance above is the schema's verdict and not a silent pass.
        with self.assertRaises(ValueError):
            validate_result({**payload, "unexpected": True})


def repair(passed: bool) -> OracleEvaluation:
    return OracleEvaluation(oracle_id="qos.path.repair", version="1.1.0", phase="repair",
                            passed=passed, probes=())


def snapshot(command: str = "ip address show; ip route show; tc qdisc/class/filter show") -> dict[str, Any]:
    return {"ok": True, "devices": {
        "finance1": {"kind": "linux", "ok": True, "command": command},
        "leaf1": {"kind": "nokia_srlinux", "ok": True, "command": "info from running /"},
    }}


def record(*, passed: bool = True, changed: list[str] | None = None,
           command: str | None = None, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """A record written before `repair_without_change` existed: E12-E16 look like this."""
    taken = snapshot() if command is None else snapshot(command)
    return {
        "metrics": {"fault_applicable": True} if metrics is None else metrics,
        "evaluations": {"repair": {"passed": passed}},
        "device_configurations": {"snapshots": {"before_sut": taken, "after_sut": taken},
                                  "changed_by_sut": changed or []},
    }


class RepairWithoutChangeTests(unittest.TestCase):
    def test_a_pass_on_an_untouched_lab_is_marked(self) -> None:
        self.assertIs(True, repair_without_change(repair(True), [], fault_applicable=True))

    def test_a_pass_after_a_change_and_a_failed_repair_are_not(self) -> None:
        self.assertIs(False, repair_without_change(repair(True), ["finance1"], fault_applicable=True))
        self.assertIs(False, repair_without_change(repair(False), [], fault_applicable=True))

    def test_it_is_not_asked_where_nothing_can_answer(self) -> None:
        # A healthy lab has nothing to repair, a missing oracle no verdict, and a
        # missing snapshot no evidence that nothing changed.
        self.assertIsNone(repair_without_change(repair(True), [], fault_applicable=False))
        self.assertIsNone(repair_without_change(None, [], fault_applicable=True))
        self.assertIsNone(repair_without_change(repair(True), None, fault_applicable=True))

    def test_the_marker_validates_in_a_result(self) -> None:
        metrics = calculate_shared_metrics(passing_phases(), OperationCounts())
        for value in (True, False, None):
            validate_result(result_payload({**metrics, "repair_without_change": value}))


class RepairWithoutChangeOfRecordTests(unittest.TestCase):
    def test_the_recorded_marker_is_read_as_it_is(self) -> None:
        recorded = record(changed=["finance1"], metrics={"repair_without_change": True})
        self.assertIs(True, repair_without_change_of(recorded))
        self.assertIsNone(repair_without_change_of(record(metrics={"repair_without_change": None})))

    def test_an_older_record_is_derived_from_its_snapshots(self) -> None:
        self.assertIs(True, repair_without_change_of(record()))
        self.assertIs(False, repair_without_change_of(record(changed=["finance1"])))
        self.assertIs(False, repair_without_change_of(record(passed=False)))
        # Records from before the no-fault episodes carry no fault_applicable.
        self.assertIs(True, repair_without_change_of(record(metrics={})))

    def test_a_snapshot_blind_to_traffic_control_cannot_vouch(self) -> None:
        # Before 2026-09-26 a Linux host was read without tc: a netem repair left no
        # trace, and "unchanged" would accuse a subject that removed the qdisc.
        self.assertIsNone(repair_without_change_of(record(command="ip address show; ip route show")))

    def test_no_fault_no_verdict_or_a_failed_snapshot_answer_none(self) -> None:
        self.assertIsNone(repair_without_change_of(record(metrics={"fault_applicable": False})))
        unjudged = record()
        unjudged["evaluations"] = {}
        self.assertIsNone(repair_without_change_of(unjudged))
        failed = record()
        failed["device_configurations"]["snapshots"]["after_sut"] = {"ok": False, "devices": {}}
        self.assertIsNone(repair_without_change_of(failed))
        self.assertIsNone(repair_without_change_of({**record(), "device_configurations": None}))

