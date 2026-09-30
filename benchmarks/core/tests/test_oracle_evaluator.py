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
