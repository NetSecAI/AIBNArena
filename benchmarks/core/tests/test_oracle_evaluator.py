from __future__ import annotations

import unittest
from typing import Any, Mapping

from benchmarks.core import OracleEvaluator


class StaticProbeRunner:
    def __init__(self, result: Mapping[str, Any]):
        self.result = result

    def run_probe(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.result


class OracleEvaluatorTests(unittest.TestCase):
    def test_baseline_relative_threshold_uses_factor_and_offset(self) -> None:
        document = {
            "oracle_id": "qos.relative",
            "version": "1.0.0",
            "phase": "repair",
            "success": {"mode": "all"},
            "probes": [
                {
                    "id": "path",
                    "thresholds": [
                        {
                            "metric": "rtt_avg_ms",
                            "operator": "lte",
                            "baseline": {"metric": "rtt_avg_ms", "factor": 1.5, "offset": 5},
                        }
                    ],
                    "success": {"mode": "all"},
                }
            ],
        }
        evaluation = OracleEvaluator(StaticProbeRunner({"rtt_avg_ms": 19})).evaluate(
            document,
            baseline={"path": {"rtt_avg_ms": 10}},
        )
        self.assertTrue(evaluation.passed)

    def test_absolute_ceiling_is_scaled_by_its_factor(self) -> None:
        """The circuit cap: the link rate plus a fifth, as an upper bound."""
        document = {
            "oracle_id": "qos.circuit",
            "version": "1.0.0",
            "phase": "repair",
            "success": {"mode": "all"},
            "probes": [
                {
                    "id": "protected_flow",
                    "thresholds": [
                        {"metric": "circuit_egress_mbps", "operator": "lte", "value": 10, "factor": 1.2}
                    ],
                    "success": {"mode": "all"},
                }
            ],
        }

        def passes(egress: Any) -> bool:
            runner = StaticProbeRunner({"circuit_egress_mbps": egress})
            return OracleEvaluator(runner).evaluate(document).passed

        self.assertTrue(passes(11.9))
        self.assertTrue(passes(12.0))
        self.assertFalse(passes(12.1))
        self.assertFalse(passes(None))

    def test_missing_metric_fails_closed(self) -> None:
        document = {
            "oracle_id": "connectivity.closed",
            "version": "1.0.0",
            "phase": "healthy",
            "success": {"mode": "all"},
            "probes": [
                {
                    "id": "path",
                    "thresholds": [
                        {"metric": "packet_loss_percent", "operator": "lte", "value": 0}
                    ],
                    "success": {"mode": "all"},
                }
            ],
        }
        evaluation = OracleEvaluator(StaticProbeRunner({})).evaluate(document)
        self.assertFalse(evaluation.passed)


if __name__ == "__main__":
    unittest.main()
