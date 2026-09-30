from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from benchmarks.core import (
    BenchmarkLifecycle,
    ExperimentConfig,
    LifecycleDependencies,
    LifecycleError,
    OperationCounts,
    OracleEvaluator,
    ResultWriter,
    SUTResponse,
    ScenarioDefinition,
)
from benchmarks.core.metrics import calculate_shared_metrics
from benchmarks.core.reporting import validate_result
from benchmarks.core.sut_client import _operation_counts
from scripts.generate_report import render_html, render_json, report_document


class FakePlatform:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.restore_result: Mapping[str, Any] = {"ok": True}
        self.inject_results: list[Mapping[str, Any]] = []

    def deploy_or_reset(self, testbed: Mapping[str, Any]) -> Mapping[str, Any]:
        self.actions.append("deploy_or_reset")
        return {"ok": True}

    def apply_reference_state(self, reference_state: str) -> Mapping[str, Any]:
        self.actions.append("apply_reference_state")
        return {"ok": True}

    def inject_fault(self, fault: Mapping[str, Any]) -> Mapping[str, Any]:
        self.actions.append("inject_fault")
        if self.inject_results:
            return dict(self.inject_results.pop(0))
        return {"ok": True}

    def wait_for_convergence(self) -> Mapping[str, Any]:
        self.actions.append("wait_for_convergence")
        return {"ok": True, "converged": True}

    def restore_fault(self) -> Mapping[str, Any]:
        self.actions.append("restore_fault")
        return dict(self.restore_result)

    def restore_or_destroy(self, cleanup: str) -> Mapping[str, Any]:
        self.actions.append(f"cleanup:{cleanup}")
        return {"ok": True, "mode": cleanup}


HEALTHY_DRAW = {"packet_loss_percent": 0.0, "rtt_avg_ms": 2.0}
DEGRADED_DRAW = {"packet_loss_percent": 100.0, "rtt_avg_ms": None}


class QueuedProbeRunner:
    """Hands out one queued draw per probe; an Exception in the queue is raised."""

    def __init__(self, results: list[Mapping[str, Any] | Exception] | None = None) -> None:
        self.results = iter(
            results
            if results is not None
            else [
                HEALTHY_DRAW,
                DEGRADED_DRAW,
                {"packet_loss_percent": 0.0, "rtt_avg_ms": 2.5},
                {"packet_loss_percent": 0.0, "rtt_avg_ms": 3.0},
            ]
        )

    def run_probe(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class FakeSUT:
    def __init__(self) -> None:
        self.last_task: Mapping[str, Any] | None = None

    def invoke(self, task: Mapping[str, Any]) -> SUTResponse:
        self.last_task = task
        return SUTResponse(
            result={"status": "completed", "verified": True},
            identity="test-sut",
            version="1.2.3",
            reported_model="reported/model",
            provider_model="provider/model",
            operations=OperationCounts(
                llm_calls=1,
                ani_reads=2,
                ani_mutations=1,
                validations=1,
                successful_mutations=1,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
            runtime_parameters={"temperature": 0, "recursion_limit": 1000},
        )


def oracle(phase: str, *, degraded: bool = False, probe_id: str = "path") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "oracle_id": f"connectivity.test.{phase}",
        "version": "1.0.0",
        "domain": "connectivity",
        "phase": phase,
        "probes": [
            {
                "id": probe_id,
                "type": "icmp",
                "source": "user1",
                "destination": "10.0.0.2",
                "protocol": "icmp",
                "attempts": 1,
                "timeout_seconds": 1,
                "metrics": ["packet_loss_percent"],
                "thresholds": [
                    {
                        "metric": "packet_loss_percent",
                        "operator": "gte" if degraded else "lte",
                        "value": 100 if degraded else 0,
                    }
                ],
                "aggregation": {"method": "mean"},
                "success": {"mode": "all"},
            }
        ],
        "success": {"mode": "all"},
    }


def _no_fault_scenario(root: Path) -> ScenarioDefinition:
    topology = root / "topology.yml"
    reference = root / "healthy.json"
    topology.write_text("name: test\n", encoding="utf-8")
    reference.write_text("{}\n", encoding="utf-8")
    return ScenarioDefinition.from_mapping({
        "id": "golden_connectivity",
        "version": "1.0.0",
        "domain": "connectivity",
        "intent": "restore connectivity",
        "topology": str(topology),
        "reference_state": str(reference),
        "fault": {"operation": "disable_interface"},
        "bindings": {},
        "fault_applicable": False,
        # The compiled instance still references its degradation oracle; a
        # no-fault run does not load it, so the reference is left in place here
        # exactly as the scenario files carry it.
        "oracles": {phase: {"path": phase, "version": "1.0.0"}
                    for phase in ("healthy", "expected_degradation", "repair", "preservation")},
    })


class RefusedSUT(FakeSUT):
    """A subject whose one mutation attempt the ANI refused: it still asked to write."""

    def invoke(self, task: Mapping[str, Any]) -> SUTResponse:
        response = super().invoke(task)
        return SUTResponse(
            result=response.result, identity=response.identity, version=response.version,
            reported_model=response.reported_model, provider_model=response.provider_model,
            operations=OperationCounts(llm_calls=1, ani_reads=2, ani_mutations=1,
                                       failed_operations=1, validations=1),
            runtime_parameters=response.runtime_parameters,
        )


class QuietSUT(FakeSUT):
    """A subject that reads, validates and changes nothing."""

    def invoke(self, task: Mapping[str, Any]) -> SUTResponse:
        response = super().invoke(task)
        return SUTResponse(
            result=response.result, identity=response.identity, version=response.version,
            reported_model=response.reported_model, provider_model=response.provider_model,
            operations=OperationCounts(llm_calls=1, ani_reads=2, validations=1),
            runtime_parameters=response.runtime_parameters,
        )


class LifecycleTests(unittest.TestCase):
    def test_complete_lifecycle_records_provenance_and_disjoint_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            topology = root / "topology.yml"
            reference = root / "healthy.json"
            topology.write_text("name: test\n", encoding="utf-8")
            reference.write_text("{}\n", encoding="utf-8")
            output = root / "results"
            scenario = ScenarioDefinition.from_mapping(
                {
                    "id": "golden_connectivity",
                    "version": "1.0.0",
                    "domain": "connectivity",
                    "intent": "restore connectivity",
                    "topology": str(topology),
                    "reference_state": str(reference),
                    "fault": {"operation": "disable_interface"},
                    "bindings": {},
                    "oracles": {
                        phase: {"path": phase, "version": "1.0.0"}
                        for phase in (
                            "healthy",
                            "expected_degradation",
                            "repair",
                            "preservation",
                        )
                    },
                }
            )
            documents = {
                "healthy": oracle("healthy"),
                "expected_degradation": oracle("expected_degradation", degraded=True),
                "repair": oracle("repair"),
                "preservation": oracle("preservation", probe_id="preserved"),
            }
            config = ExperimentConfig(
                experiment_id="golden",
                scenario_path="scenario.yaml",
                testbed={"platform": "fake"},
                execution_budget_seconds=400,
                result_dir=str(output),
            )
            platform = FakePlatform()
            phase_starts: list[str] = []
            lifecycle = BenchmarkLifecycle(
                LifecycleDependencies(
                    config_loader=lambda path: config,
                    scenario_loader=lambda path: scenario,
                    oracle_loader=lambda path, version: documents[str(path)],
                    platform=platform,
                    evaluator=OracleEvaluator(QueuedProbeRunner()),
                    sut_client=FakeSUT(),
                    result_sink=ResultWriter(),
                    repository=Path(__file__).resolve().parents[3],
                    trace_sink=lambda event, payload: (
                        phase_starts.append(str(payload["phase"]))
                        if event == "phase_started"
                        else None
                    ),
                )
            )

            result_path = lifecycle.run("experiment.toml")
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            validate_result(payload)
            document = report_document(payload, result_path, result_path.read_bytes())
            rendered = render_html(document)
            self.assertIn(payload["run_id"], rendered)
            self.assertIn("Operation counts", rendered)
            self.assertIn("Oracle evaluations", rendered)
            # The two renderings are the same document, so the JSON one is exercised
            # by the same payload rather than by a fixture of its own.
            self.assertEqual(document, json.loads(render_json(document)))

            self.assertEqual(list(BenchmarkLifecycle.PHASES), phase_starts)
            self.assertEqual(
                [
                    "deploy_or_reset",
                    "apply_reference_state",
                    "inject_fault",
                    "wait_for_convergence",
                    "cleanup:restore",
                ],
                platform.actions,
            )
            self.assertEqual(set(BenchmarkLifecycle.PHASES), set(payload["phase_durations"]))
            self.assertTrue(all(value >= 0 for value in payload["phase_durations"].values()))
            self.assertEqual(2, payload["operation_counts"]["ani_reads"])
            self.assertEqual(1, payload["operation_counts"]["ani_mutations"])
            self.assertEqual(1, payload["operation_counts"]["successful_mutations"])
            self.assertEqual(5, payload["operation_counts"]["validations"])
            # The default single attempt is still recorded as an attempt log.
            self.assertEqual(
                {
                    "attempts_allowed": 1,
                    "attempts": [
                        {"attempt": 1, "restored": None, "reinjected": None, "degraded": True, "voided": False},
                    ],
                },
                payload["evaluations"]["expected_degradation"]["injection"],
            )
            # The log lives in exactly one place on a run that reached the evaluation.
            self.assertNotIn("injection", payload["metrics"])
            self.assertEqual(1, payload["operation_counts"]["llm_calls"])
            # The judge was not told a model here, and says so rather than
            # copying the subject's answer into the field meant to check it.
            self.assertIsNone(payload["provenance"]["configured_model"])
            self.assertEqual("reported/model", payload["provenance"]["sut_reported_model"])
            self.assertEqual("provider/model", payload["provenance"]["provider_reported_model"])
            self.assertEqual(0, payload["provenance"]["model_parameters"]["temperature"])
            self.assertEqual({"ok": True, "converged": True}, payload["convergence_result"])
            self.assertTrue(payload["metrics"]["success"])
            self.assertTrue(payload["metrics"]["environment_success"])
            self.assertTrue(payload["metrics"]["converged"])
            self.assertTrue(payload["metrics"]["sut_completed"])
            self.assertTrue(payload["metrics"]["sut_verified"])
            self.assertEqual({"ok": True, "mode": "restore"}, payload["cleanup_result"])
            self.assertRegex(payload["scenario"]["sut_task_id"], r"^sut-[0-9a-f]{32}$")
            self.assertIsNone(payload["error"])

    def test_sut_failure_is_persisted_and_cleanup_failure_does_not_mask_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            topology = root / "topology.yml"
            reference = root / "healthy.json"
            topology.write_text("name: test\n", encoding="utf-8")
            reference.write_text("{}\n", encoding="utf-8")
            scenario = ScenarioDefinition.from_mapping({
                "id": "private_scenario_name",
                "version": "1.0.0",
                "domain": "connectivity",
                "intent": "restore connectivity",
                "topology": str(topology),
                "reference_state": str(reference),
                "fault": {"operation": "disable_interface"},
                "bindings": {},
                "oracles": {
                    phase: {"path": phase, "version": "1.0.0"}
                    for phase in ("healthy", "expected_degradation", "repair", "preservation")
                },
            })
            documents = {
                "healthy": oracle("healthy"),
                "expected_degradation": oracle("expected_degradation", degraded=True),
                "repair": oracle("repair"),
                "preservation": oracle("preservation", probe_id="preserved"),
            }
            config = ExperimentConfig(
                experiment_id="failure",
                scenario_path="scenario.yaml",
                testbed={"platform": "fake"},
                result_dir=str(root / "results"),
            )

            class FailingSUT:
                task: Mapping[str, Any] | None = None

                def invoke(self, task: Mapping[str, Any]) -> SUTResponse:
                    self.task = task
                    raise RuntimeError("provider disconnected")

            class FailingCleanupPlatform(FakePlatform):
                def restore_or_destroy(self, cleanup: str) -> Mapping[str, Any]:
                    self.actions.append(f"cleanup:{cleanup}")
                    raise RuntimeError("cleanup also failed")

            sut = FailingSUT()
            lifecycle = BenchmarkLifecycle(LifecycleDependencies(
                config_loader=lambda path: config,
                scenario_loader=lambda path: scenario,
                oracle_loader=lambda path, version: documents[str(path)],
                platform=FailingCleanupPlatform(),
                evaluator=OracleEvaluator(QueuedProbeRunner()),
                sut_client=sut,
                result_sink=ResultWriter(),
                repository=Path(__file__).resolve().parents[3],
            ))

            with self.assertRaises(LifecycleError) as captured:
                lifecycle.run("experiment.toml")
            self.assertIsNotNone(captured.exception.result_path)
            payload = json.loads(captured.exception.result_path.read_text(encoding="utf-8"))
            validate_result(payload)
            self.assertEqual("failed", payload["status"])
            self.assertEqual("invoke_sut", payload["error"]["phase"])
            self.assertEqual("provider disconnected", payload["error"]["message"])
            self.assertFalse(payload["cleanup_result"]["ok"])
            self.assertNotEqual("private_scenario_name", sut.task["scenario_id"])
            self.assertEqual(payload["scenario"]["sut_task_id"], sut.task["scenario_id"])


def _retry_fixture(root: Path, *, experiment_id: str, injection_attempts: int) -> tuple[Any, Any, Any]:
    topology = root / "topology.yml"
    reference = root / "healthy.json"
    topology.write_text("name: test\n", encoding="utf-8")
    reference.write_text("{}\n", encoding="utf-8")
    scenario = ScenarioDefinition.from_mapping({
        "id": "retry_scenario",
        "version": "1.0.0",
        "domain": "connectivity",
        "intent": "restore connectivity",
        "topology": str(topology),
        "reference_state": str(reference),
        "fault": {"operation": "disable_interface"},
        "bindings": {},
        "oracles": {
            phase: {"path": phase, "version": "1.0.0"}
            for phase in ("healthy", "expected_degradation", "repair", "preservation")
        },
    })
    documents = {
        "healthy": oracle("healthy"),
        "expected_degradation": oracle("expected_degradation", degraded=True),
        "repair": oracle("repair"),
        "preservation": oracle("preservation", probe_id="preserved"),
    }
    config = ExperimentConfig(
        experiment_id=experiment_id,
        scenario_path="scenario.yaml",
        testbed={"platform": "fake"},
        result_dir=str(root / "results"),
        injection_attempts=injection_attempts,
    )
    return scenario, documents, config


def _retry_lifecycle(scenario, documents, config, platform, probe_results) -> BenchmarkLifecycle:
    return BenchmarkLifecycle(LifecycleDependencies(
        config_loader=lambda path: config,
        scenario_loader=lambda path: scenario,
        oracle_loader=lambda path, version: documents[str(path)],
        platform=platform,
        evaluator=OracleEvaluator(QueuedProbeRunner(probe_results)),
        sut_client=FakeSUT(),
        result_sink=ResultWriter(),
        repository=Path(__file__).resolve().parents[3],
    ))


class InjectionLogPersistenceTests(unittest.TestCase):
    """What the degradation phase did to see the fault is part of the record."""

    def test_a_retry_seen_on_the_second_draw_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario, documents, config = _retry_fixture(
                root, experiment_id="retry", injection_attempts=3
            )
            platform = FakePlatform()
            lifecycle = _retry_lifecycle(scenario, documents, config, platform, [
                HEALTHY_DRAW,   # healthy
                HEALTHY_DRAW,   # draw 1 did not see the fault
                DEGRADED_DRAW,  # draw 2 did
                HEALTHY_DRAW,   # repair
                HEALTHY_DRAW,   # preservation
            ])

            result_path = lifecycle.run("experiment.toml")
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            validate_result(payload)
            render_html(report_document(payload, result_path, result_path.read_bytes()))

            self.assertEqual("completed", payload["status"])
            self.assertEqual(
                {
                    "attempts_allowed": 3,
                    "attempts": [
                        {"attempt": 1, "restored": None, "reinjected": None, "degraded": False, "voided": False},
                        {"attempt": 2, "restored": True, "reinjected": True, "degraded": True, "voided": False},
                    ],
                },
                payload["evaluations"]["expected_degradation"]["injection"],
            )
            # healthy + two measured draws + repair + preservation + the SUT's own one.
            self.assertEqual(6, payload["operation_counts"]["validations"])
            self.assertEqual(
                ["deploy_or_reset", "apply_reference_state", "inject_fault",
                 "restore_fault", "inject_fault", "wait_for_convergence", "cleanup:restore"],
                platform.actions,
            )

    def test_a_failed_restore_is_persisted_without_a_phantom_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario, documents, config = _retry_fixture(
                root, experiment_id="restore-failed", injection_attempts=3
            )
            platform = FakePlatform()
            platform.restore_result = {"ok": False, "error": "commit refused"}
            lifecycle = _retry_lifecycle(scenario, documents, config, platform, [
                HEALTHY_DRAW,  # healthy
                HEALTHY_DRAW,  # draw 1 did not see the fault, then restore fails
            ])

            with self.assertRaises(LifecycleError) as captured:
                lifecycle.run("experiment.toml")
            payload = json.loads(captured.exception.result_path.read_text(encoding="utf-8"))
            validate_result(payload)

            self.assertEqual("failed", payload["status"])
            self.assertEqual("evaluate_degradation", payload["error"]["phase"])
            injection = payload["evaluations"]["expected_degradation"]["injection"]
            self.assertEqual(3, injection["attempts_allowed"])
            self.assertEqual(
                [
                    {"attempt": 1, "restored": None, "reinjected": None, "degraded": False, "voided": False},
                    {"attempt": 2, "restored": False, "reinjected": None, "degraded": None,
                     "error": "commit refused"},
                ],
                injection["attempts"],
            )
            # healthy + the one draw that measured; the aborted attempt ran no oracle
            # and the SUT was never invoked.
            self.assertEqual(2, payload["operation_counts"]["validations"])
            self.assertNotIn("inject_fault", platform.actions[3:])

    def test_attempts_made_before_the_evaluator_raised_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario, documents, config = _retry_fixture(
                root, experiment_id="evaluator-raised", injection_attempts=3
            )
            platform = FakePlatform()
            lifecycle = _retry_lifecycle(scenario, documents, config, platform, [
                HEALTHY_DRAW,  # healthy
                HEALTHY_DRAW,  # draw 1 did not see the fault
                RuntimeError("probe host unreachable"),  # draw 2 never measured
            ])

            with self.assertRaises(LifecycleError) as captured:
                lifecycle.run("experiment.toml")
            result_path = captured.exception.result_path
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            validate_result(payload)
            render_html(report_document(payload, result_path, result_path.read_bytes()))

            self.assertEqual("failed", payload["status"])
            self.assertEqual("evaluate_degradation", payload["error"]["phase"])
            self.assertEqual("probe host unreachable", payload["error"]["message"])
            # No degradation evaluation exists to carry the log, so it rides on
            # metrics, the other open object under the closed result root.
            self.assertNotIn("expected_degradation", payload["evaluations"])
            self.assertEqual(
                {
                    "attempts_allowed": 3,
                    "attempts": [
                        {"attempt": 1, "restored": None, "reinjected": None, "degraded": False, "voided": False},
                    ],
                },
                payload["metrics"]["injection"],
            )
            # healthy + the one draw that measured before the evaluator raised.
            self.assertEqual(2, payload["operation_counts"]["validations"])
            self.assertEqual(
                ["deploy_or_reset", "apply_reference_state", "inject_fault",
                 "restore_fault", "inject_fault", "cleanup:restore"],
                platform.actions,
            )


class EarlyFailureTests(unittest.TestCase):
    """A run that dies before the degradation phase still leaves a result behind."""

    def test_a_failed_deployment_still_writes_a_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario, documents, config = _retry_fixture(
                root, experiment_id="deploy-failed", injection_attempts=1
            )

            class UndeployablePlatform(FakePlatform):
                def deploy_or_reset(self, testbed: Mapping[str, Any]) -> Mapping[str, Any]:
                    self.actions.append("deploy_or_reset")
                    return {"ok": False, "error": "containerlab refused"}

            platform = UndeployablePlatform()
            lifecycle = _retry_lifecycle(scenario, documents, config, platform, [])

            with self.assertRaises(LifecycleError) as captured:
                lifecycle.run("experiment.toml")
            result_path = captured.exception.result_path
            self.assertIsNotNone(result_path)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            validate_result(payload)
            render_html(report_document(payload, result_path, result_path.read_bytes()))

            self.assertEqual("failed", payload["status"])
            self.assertEqual("deploy_reset_testbed", payload["error"]["phase"])
            self.assertEqual(
                "testbed deployment/reset failed: containerlab refused",
                payload["error"]["message"],
            )
            # Nothing was evaluated and no attempt was made, so no log is claimed.
            self.assertEqual({}, payload["evaluations"])
            self.assertNotIn("injection", payload["metrics"])
            self.assertEqual(0, payload["operation_counts"]["validations"])
            self.assertEqual(["deploy_or_reset", "cleanup:restore"], platform.actions)


def test_operation_counts_keep_ani_categories_disjoint_and_do_not_double_count_unsafe():
    counts = _operation_counts({
        "ani_operations": [
            {"operation": "get_state", "category": "read", "ok": True},
            {"operation": "update_config", "category": "mutation", "ok": False},
            {"operation": "execute_validation", "category": "validation", "ok": True},
        ],
        "device_changes": [{"result": {"safe": False}}],
        "execution": {
            "llm_calls": 2,
            "unsafe_operations": 1,
            "token_usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        },
    })

    assert counts.llm_calls == 2
    assert counts.ani_reads == 1
    assert counts.ani_mutations == 1
    assert counts.successful_mutations == 0
    assert counts.validations == 1
    assert counts.failed_operations == 1
    assert counts.unsafe_operations == 1
    assert (counts.input_tokens, counts.output_tokens, counts.total_tokens) == (8, 3, 11)


def test_operation_counts_keep_a_refused_read_as_an_attempt_but_not_a_success():
    counts = _operation_counts({
        "ani_operations": [
            {
                "operation": "get_state",
                "category": "read",
                "ok": False,
                "error": {"type": "refused", "message": "get_state is not on this subject's surface"},
            },
            {"operation": "get_topology", "category": "read", "ok": True},
            {"operation": "execute_validation", "category": "validation", "ok": False},
        ],
        "execution": {"llm_calls": 1},
    })

    # The refusal was still an attempt, like a failed mutation: counted as a read and
    # as a failed operation, but not as a successful read.
    assert counts.ani_reads == 2
    assert counts.successful_reads == 1
    assert counts.failed_operations == 2
    # A validation that ran and failed is still a validation.
    assert counts.validations == 1


if __name__ == "__main__":
    unittest.main()


class NoFaultEpisodeTests(unittest.TestCase):
    """The false-positive episode: the same plate, nothing injected."""

    def _run(self, sut, draws) -> tuple[dict[str, Any], FakePlatform, list[str]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = _no_fault_scenario(root)
            self.assertNotIn("expected_degradation", scenario.oracles)
            self.assertFalse(scenario.fault_applicable)
            documents = {
                "healthy": oracle("healthy"),
                "repair": oracle("repair"),
                "preservation": oracle("preservation", probe_id="preserved"),
            }
            config = ExperimentConfig(
                experiment_id="no-fault", scenario_path="scenario.yaml",
                testbed={"platform": "fake"}, execution_budget_seconds=400,
                result_dir=str(root / "results"))
            platform = FakePlatform()
            phases: list[str] = []
            lifecycle = BenchmarkLifecycle(LifecycleDependencies(
                config_loader=lambda path: config,
                scenario_loader=lambda path: scenario,
                oracle_loader=lambda path, version: documents[str(path)],
                platform=platform,
                evaluator=OracleEvaluator(QueuedProbeRunner(draws)),
                sut_client=sut,
                result_sink=ResultWriter(),
                repository=Path(__file__).resolve().parents[3],
                trace_sink=lambda event, payload: (
                    phases.append(str(payload["phase"])) if event == "phase_started" else None),
            ))
            path = lifecycle.run("experiment.toml")
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_result(payload)
            return payload, platform, phases

    def test_nothing_is_injected_and_a_quiet_subject_passes(self) -> None:
        payload, platform, phases = self._run(QuietSUT(), [HEALTHY_DRAW, HEALTHY_DRAW, HEALTHY_DRAW])
        self.assertNotIn("inject_fault", platform.actions)
        self.assertNotIn("inject_fault", phases)
        self.assertNotIn("evaluate_degradation", phases)
        self.assertEqual(0.0, payload["phase_durations"]["inject_fault"])
        self.assertEqual(0.0, payload["phase_durations"]["evaluate_degradation"])
        self.assertEqual({"healthy", "repair", "preservation"}, set(payload["evaluations"]))
        self.assertFalse(payload["metrics"]["fault_applicable"])
        self.assertFalse(payload["metrics"]["false_positive"])
        self.assertTrue(payload["metrics"]["success"])
        self.assertEqual("completed", payload["status"])

    def test_a_successful_mutation_is_the_false_positive(self) -> None:
        # FakeSUT reports one successful mutation on a lab that needed none.
        payload, _, _ = self._run(FakeSUT(), [HEALTHY_DRAW, HEALTHY_DRAW, HEALTHY_DRAW])
        self.assertTrue(payload["metrics"]["false_positive"])
        self.assertFalse(payload["metrics"]["success"])
        self.assertTrue(payload["metrics"]["environment_success"] is False)
        self.assertTrue(payload["metrics"]["phase_passed"]["repair"])

    def test_a_refused_attempt_is_also_the_false_positive(self) -> None:
        # One mutation attempted, refused by the ANI. Nothing reached the device, so
        # every oracle still passes and the counters record no successful mutation --
        # and the episode still fails, because asking to write on a healthy lab is
        # the misdiagnosis this plate measures. The guard held; that is not the
        # subject's doing.
        payload, _, _ = self._run(RefusedSUT(), [HEALTHY_DRAW, HEALTHY_DRAW, HEALTHY_DRAW])
        self.assertEqual(1, payload["operation_counts"]["ani_mutations"])
        self.assertEqual(0, payload["operation_counts"]["successful_mutations"])
        self.assertTrue(payload["metrics"]["phase_passed"]["repair"])
        self.assertTrue(payload["metrics"]["false_positive"])
        self.assertFalse(payload["metrics"]["success"])

    def test_a_fault_episode_does_not_ask_the_question(self) -> None:
        metrics = calculate_shared_metrics_for_test()
        self.assertTrue(metrics["fault_applicable"])
        self.assertIsNone(metrics["false_positive"])


def calculate_shared_metrics_for_test() -> dict[str, Any]:
    from benchmarks.core.metrics import calculate_shared_metrics
    from benchmarks.core.oracle_evaluator import OracleEvaluation
    evaluations = {phase: OracleEvaluation(oracle_id=phase, version="1.0.0", phase=phase, passed=True, probes=())
                   for phase in ("healthy", "expected_degradation", "repair")}
    return calculate_shared_metrics(evaluations, OperationCounts(successful_mutations=3))


def test_a_throughput_criterion_hands_the_subject_the_flow_it_must_measure() -> None:
    """The subject's own observed_throughput check reads observation.service_measurement."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        topology = root / "topology.yml"; reference = root / "healthy.json"
        topology.write_text("name: test\n", encoding="utf-8"); reference.write_text("{}\n", encoding="utf-8")

        def scenario_with(criteria, bindings):
            return ScenarioDefinition.from_mapping({
                "id": "qos.wan_shaping_policy_repair.high.m1", "version": "1.0.0", "domain": "connectivity",  # the fake oracles are connectivity ones
                "intent": "restore the guarantee", "topology": str(topology), "reference_state": str(reference),
                "fault": {"operation": "degrade_policy"}, "bindings": bindings,
                "success_criteria": criteria,
                "oracles": {phase: {"path": phase, "version": "1.0.0"}
                            for phase in ("healthy", "expected_degradation", "repair", "preservation")},
            })

        documents = {
            "healthy": oracle("healthy"),
            "expected_degradation": oracle("expected_degradation", degraded=True),
            "repair": oracle("repair"),
            "preservation": oracle("preservation", probe_id="preserved"),
        }
        config = ExperimentConfig(experiment_id="qos", scenario_path="scenario.yaml", testbed={"platform": "fake"},
                                  execution_budget_seconds=400, result_dir=str(root / "results"))
        qos_criteria = {"all_of": [{"type": "lab_connectivity"},
                                   {"type": "observed_throughput", "min_mbps": "8", "under_contention": True}]}
        bindings = {"target": "wan1", "protected_source": "user1", "destination": "external1",
                    "destination_ip": "203.0.113.10", "measurement_port": 5201}
        for criteria, expected in ((qos_criteria, {"service_measurement": {
                                        "source": "user1", "destination": "external1",
                                        "destination_ip": "203.0.113.10"}}),
                                   ({"all_of": [{"type": "lab_connectivity"}]}, None)):
            sut = FakeSUT()
            lifecycle = BenchmarkLifecycle(LifecycleDependencies(
                config_loader=lambda path: config, scenario_loader=lambda path, s=criteria: scenario_with(s, bindings),
                oracle_loader=lambda path, version: documents[str(path)], platform=FakePlatform(),
                evaluator=OracleEvaluator(QueuedProbeRunner()), sut_client=sut, result_sink=ResultWriter(),
                repository=Path(__file__).resolve().parents[3], trace_sink=lambda event, payload: None))
            lifecycle.run("experiment.toml")
            assert sut.last_task["observation"] == expected, criteria
            assert sut.last_task["success_criteria"] == criteria
