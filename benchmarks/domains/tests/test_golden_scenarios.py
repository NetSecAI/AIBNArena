from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from benchmarks.configs import load_experiment, load_scenario
from benchmarks.core import BenchmarkLifecycle, LifecycleDependencies, OperationCounts, OracleEvaluator, ResultWriter, SUTResponse
from benchmarks.core.contracts import OracleEvaluation, ProbeEvaluation
from benchmarks.domains.metrics import domain_metrics, for_domain
from benchmarks.platforms.containerlab import ContainerlabPlatform, ProbeExecutionError
from scenarios.oracle_loader import load_oracle


ROOT = Path(__file__).resolve().parents[3]


class FakePlatform:
    def deploy_or_reset(self, testbed):
        return {"ok": True}

    def apply_reference_state(self, reference_state):
        return {"ok": True}

    def inject_fault(self, fault):
        return {"ok": True}
    def wait_for_convergence(self):
        return {"ok": True, "converged": True}


    def restore_or_destroy(self, cleanup):
        return {"ok": True, "mode": cleanup}


class GoldenProbes:
    def __init__(self, domain: str):
        if domain == "connectivity":
            self.values = {
                "required_paths": iter([
                    {"success": True, "total_checks": 10, "passed_checks": 10, "failed_checks": 0},
                    {"success": True, "total_checks": 10, "passed_checks": 10, "failed_checks": 0},
                ]),
                "affected_paths": iter([
                    {"success": False, "total_checks": 4, "passed_checks": 2, "failed_checks": 2}
                ]),
                "unaffected_paths": iter([
                    {"success": True, "total_checks": 6, "passed_checks": 6, "failed_checks": 0}
                ]),
            }
        else:
            self.values = {
                "primary_path_quality": iter([
                    {"packet_loss_percent": 0.0, "rtt_avg_ms": 1.0},
                    {"packet_loss_percent": 30.0, "rtt_avg_ms": 100.0},
                    {"packet_loss_percent": 0.0, "rtt_avg_ms": 1.5},
                ]),
                "required_paths": iter([
                    {"success": True, "total_checks": 10, "passed_checks": 10, "failed_checks": 0},
                ]),
                "unaffected_paths": iter([
                    {"success": True, "total_checks": 6, "passed_checks": 6, "failed_checks": 0},
                ]),
            }

    def run_probe(self, probe: Mapping[str, Any]):
        return next(self.values[str(probe["id"])])


class FakeSUT:
    def invoke(self, task):
        assert task["scenario_id"].startswith("sut-")
        assert "smoke" not in task["scenario_id"]
        return SUTResponse(
            result={"mode": "self_execute", "status": "completed", "verified": True, "device_changes": []},
            identity="golden-sut",
            version="1.0.0",
            reported_model="golden/model",
            provider_model="provider/model-revision",
            operations=OperationCounts(llm_calls=2, ani_reads=3, ani_mutations=1, validations=1, total_tokens=42),
        )


@pytest.mark.parametrize("domain", ["connectivity", "qos"])
def test_domain_golden_runs_use_the_shared_lifecycle(domain: str, tmp_path: Path):
    config_path = ROOT / f"benchmarks/configs/experiments/{domain}-smoke.toml"
    config = replace(load_experiment(config_path), result_dir=str(tmp_path))
    scenario = load_scenario(config.scenario_path)
    platform = FakePlatform()
    lifecycle = BenchmarkLifecycle(LifecycleDependencies(
        config_loader=lambda path: config,
        scenario_loader=lambda path: scenario,
        oracle_loader=lambda path, version: load_oracle(path, expected_version=version),
        platform=platform,
        evaluator=OracleEvaluator(GoldenProbes(domain)),
        sut_client=FakeSUT(),
        result_sink=ResultWriter(),
        repository=ROOT,
        domain_metrics=for_domain(domain),
    ))
    result = json.loads(lifecycle.run(config_path).read_text(encoding="utf-8"))
    assert result["scenario"]["domain"] == domain
    assert result["metrics"]["success"] is True
    assert result["metrics"]["environment_success"] is True
    assert result["metrics"]["non_regression_passed"] is True
    # The golden probes measure six unaffected paths, so the pass is not vacuous.
    assert result["metrics"]["preservation_vacuous"] is False
    assert result["metrics"]["non_regression_vacuous"] is False
    assert result["operation_counts"]["ani_reads"] == 3
    assert set(result["phase_durations"]) == set(BenchmarkLifecycle.PHASES)


@pytest.mark.parametrize("probe_type", ["security_event", "configuration"])
def test_unimplemented_security_collectors_fail_closed(probe_type: str):
    with pytest.raises(ProbeExecutionError, match="no implementation was declared"):
        ContainerlabPlatform().run_probe({"type": probe_type})


def test_security_domain_metrics_are_executable_without_infrastructure():
    class Evaluation:
        passed = True
        probes = ()

    metrics = for_domain("security")(
        {"repair": Evaluation(), "preservation": Evaluation()},
        {"verified": True},
    )
    assert metrics == {
        "domain": "security",
        "control_repair_pass_rate": 0.0,
        "non_regression_passed": True,
        # A stub evaluation with no probes measured nothing, so its pass is vacuous.
        "non_regression_vacuous": True,
        "sut_verified": True,
    }


def test_domain_metrics_surface_the_vacuous_preservation_marker():
    def evaluation(phase: str, metrics: Mapping[str, Any]) -> OracleEvaluation:
        return OracleEvaluation(
            oracle_id=f"connectivity.{phase}",
            version="1.0.0",
            phase=phase,
            passed=True,
            probes=(ProbeEvaluation(
                probe_id="unaffected_paths",
                passed=True,
                metrics=dict(metrics),
                thresholds=({"metric": "failed_checks", "operator": "lte", "value": 0},),
                threshold_results=(True,),
            ),),
        )

    vacuous = {"success": True, "total_checks": 0, "passed_checks": 0, "failed_checks": 0, "checks": [], "vacuous": True}
    measured = {"success": True, "total_checks": 6, "passed_checks": 6, "failed_checks": 0, "checks": []}
    repair = evaluation("repair", measured)

    marked = domain_metrics(
        "connectivity", {"repair": repair, "preservation": evaluation("preservation", vacuous)}, {"verified": True}
    )
    assert marked["non_regression_passed"] is True
    assert marked["non_regression_vacuous"] is True

    unmarked = domain_metrics(
        "connectivity", {"repair": repair, "preservation": evaluation("preservation", measured)}, {"verified": True}
    )
    assert unmarked["non_regression_passed"] is True
    assert unmarked["non_regression_vacuous"] is False

    absent = domain_metrics("connectivity", {"repair": repair}, {"verified": True})
    assert absent["non_regression_passed"] is None
    assert absent["non_regression_vacuous"] is None
