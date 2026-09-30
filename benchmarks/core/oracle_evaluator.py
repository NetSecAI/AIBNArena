"""Generic evaluation of validated declarative oracles."""
from __future__ import annotations

import operator
from typing import Any, Mapping

from .contracts import OracleEvaluation, ProbeEvaluation, ProbeRunner


_OPERATORS = {
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
    "gte": operator.ge,
    "gt": operator.gt,
}


class OracleEvaluator:
    def __init__(self, probe_runner: ProbeRunner):
        self.probe_runner = probe_runner

    def evaluate(
        self,
        oracle: Mapping[str, Any],
        *,
        baseline: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> OracleEvaluation:
        probes: list[ProbeEvaluation] = []
        for probe in oracle["probes"]:
            metrics = dict(self.probe_runner.run_probe(probe))
            checks = tuple(
                self._threshold_passes(threshold, metrics, baseline or {}, str(probe["id"]))
                for threshold in probe["thresholds"]
            )
            probe_passed = _combine(checks, probe["success"])
            probes.append(
                ProbeEvaluation(
                    probe_id=str(probe["id"]),
                    passed=probe_passed,
                    metrics=metrics,
                    thresholds=tuple(dict(item) for item in probe["thresholds"]),
                    threshold_results=checks,
                )
            )
        passed = _combine(tuple(item.passed for item in probes), oracle["success"])
        return OracleEvaluation(
            oracle_id=str(oracle["oracle_id"]),
            version=str(oracle["version"]),
            phase=str(oracle["phase"]),
            passed=passed,
            probes=tuple(probes),
        )

    @staticmethod
    def _threshold_passes(
        threshold: Mapping[str, Any],
        metrics: Mapping[str, Any],
        baseline: Mapping[str, Mapping[str, Any]],
        probe_id: str,
    ) -> bool:
        metric = str(threshold["metric"])
        actual = metrics.get(metric)
        if actual is None:
            return False
        absolute = None
        if "value" in threshold:
            # `factor` and `offset` scale the absolute bound the way they scale a
            # baseline one, so a bar expressed as a fraction of a contractual figure
            # -- half the assured rate, say -- stays a declared threshold rather than
            # a number the scenario has to precompute into its bindings.
            absolute = (
                float(threshold["value"]) * float(threshold.get("factor", 1))
                + float(threshold.get("offset", 0))
            )
        relative_expected = None
        if "baseline" in threshold:
            relative = threshold["baseline"]
            baseline_probe = baseline.get(probe_id, {})
            baseline_value = baseline_probe.get(str(relative["metric"]))
            if baseline_value is None:
                # A threshold that asked to be read against a reference cannot be
                # judged without one, even when it also carries an absolute bound:
                # falling back to the absolute would quietly change the question.
                return False
            relative_expected = (
                float(baseline_value) * float(relative["factor"])
                + float(relative.get("offset", 0))
            )

        if absolute is not None and relative_expected is not None:
            # Both bounds, and `combine` says which one governs. `min` is the bar a
            # measurement must clear when neither the contractual figure nor what the
            # reference itself managed should be exceeded -- an assured rate the intact
            # policy over-delivered on must not become a bar correct repairs fail.
            combine = str(threshold["combine"])
            expected = (min if combine == "min" else max)(float(absolute), relative_expected)
        elif relative_expected is not None:
            expected = relative_expected
        else:
            expected = absolute
        try:
            return bool(_OPERATORS[str(threshold["operator"])](actual, expected))
        except (KeyError, TypeError, ValueError):
            return False


def _combine(values: tuple[bool, ...], success: Mapping[str, Any]) -> bool:
    mode = success["mode"]
    if mode == "all":
        return bool(values) and all(values)
    if mode == "any":
        return any(values)
    if not values:
        return False
    return sum(values) / len(values) >= float(success["minimum_pass_rate"])
