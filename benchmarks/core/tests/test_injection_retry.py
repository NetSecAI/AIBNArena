"""The degradation phase re-injects while the fault goes unseen.

Ported from the QoS runner these attempts used to live in. On a dual-homed edge the
measured flow's path is a per-flow hash, so a single draw that failed to see the
fault is not evidence the fault is absent -- but a retry has to put the reference
back first, and a restore that fails is a different finding from a fault that simply
never showed.

Everything here is faked: no lab, no SUT, no provider.
"""
from __future__ import annotations

import unittest
from typing import Any, Mapping

from benchmarks.core.contracts import OracleEvaluation, ProbeEvaluation, ScenarioDefinition
from benchmarks.core.lifecycle import LifecycleError, _inject_until_degraded, _require_oracle


class ScriptedEvaluator:
    """Answers `degraded` once per call, from a fixed script.

    An Exception instance in the script is raised on that call instead, standing
    in for a probe that could not run at all.
    """

    def __init__(self, outcomes: list[bool | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def evaluate(self, oracle: Mapping[str, Any], *, baseline=None) -> OracleEvaluation:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        # "voided": the throughput probe threw the draw out because its offered
        # contention was not observed; it fails without saying anything about the fault.
        voided = outcome == "voided"
        passed = bool(outcome) and not voided
        metrics = ({"throughput_mbps": None, "unverified_throughput_mbps": 9.4,
                    "contention_observed": False} if voided
                   else {"throughput_mbps": 0.8 if passed else 7.6})
        return OracleEvaluation(
            oracle_id="qos.test.expected_degradation",
            version="1.0.0",
            phase="expected_degradation",
            passed=passed,
            probes=(ProbeEvaluation(
                probe_id="protected_flow",
                passed=passed,
                metrics=metrics,
                thresholds=(),
                threshold_results=(),
            ),),
        )


class RecordingPlatform:
    def __init__(self, *, restore: Mapping[str, Any] | None = None,
                 reinjects: list[Mapping[str, Any]] | None = None) -> None:
        self.actions: list[str] = []
        self.restore = dict(restore or {"ok": True})
        self.reinjects = list(reinjects or [])

    def restore_fault(self) -> Mapping[str, Any]:
        self.actions.append("restore_fault")
        return dict(self.restore)

    def inject_fault(self, fault: Mapping[str, Any]) -> Mapping[str, Any]:
        self.actions.append("inject_fault")
        return dict(self.reinjects.pop(0)) if self.reinjects else {"ok": True}


class _Deps:
    def __init__(self, platform, evaluator) -> None:
        self.platform = platform
        self.evaluator = evaluator


def _scenario() -> Any:
    class _S:
        fault: Mapping[str, Any] = {"id": "qos.shaping.m1", "commands": ["tc ..."]}
    return _S()


def _run(outcomes, *, attempts, platform=None):
    platform = platform or RecordingPlatform()
    evaluator = ScriptedEvaluator(outcomes)
    log: list[dict[str, Any]] = []
    evaluation, returned = _inject_until_degraded(
        _Deps(platform, evaluator), _scenario(), {"phase": "expected_degradation"}, {},
        attempts=attempts, log=log,
    )
    # The caller's list is the log; the return value is the same object, not a copy.
    assert returned is log
    return evaluation, log, platform


class InjectionRetryTests(unittest.TestCase):
    def test_a_fault_seen_on_the_first_draw_is_not_re_injected(self) -> None:
        evaluation, log, platform = _run([True], attempts=3)
        self.assertTrue(evaluation.passed)
        self.assertEqual(1, len(log))
        self.assertEqual([], platform.actions)

    def test_a_fault_seen_on_the_second_draw_passes(self) -> None:
        evaluation, log, platform = _run([False, True], attempts=3)
        self.assertTrue(evaluation.passed)
        self.assertEqual(2, len(log))
        # The reference goes back before the fault goes in again.
        self.assertEqual(["restore_fault", "inject_fault"], platform.actions)
        self.assertEqual({"attempt": 2, "restored": True, "reinjected": True, "degraded": True,
                          "voided": False},
                         log[-1])

    def test_a_draw_thrown_out_by_its_instrument_is_marked_voided(self) -> None:
        """A flood-absent first draw is not "the fault was not seen"."""
        evaluation, log, platform = _run(["voided", True], attempts=3)
        self.assertTrue(evaluation.passed)
        self.assertEqual([True, False], [entry["voided"] for entry in log])
        self.assertEqual([False, True], [entry["degraded"] for entry in log])
        self.assertEqual(["restore_fault", "inject_fault"], platform.actions)

    def test_attempts_are_bounded(self) -> None:
        evaluation, log, platform = _run([False], attempts=3)
        self.assertFalse(evaluation.passed)
        self.assertEqual(3, len(log))
        self.assertEqual(2, platform.actions.count("inject_fault"))

    def test_a_failed_restore_ends_the_loop_at_once(self) -> None:
        platform = RecordingPlatform(restore={"ok": False, "error": "commit refused"})
        evaluation, log, platform = _run([False], attempts=3, platform=platform)
        self.assertFalse(evaluation.passed)
        self.assertEqual(2, len(log))
        self.assertIs(False, log[-1]["restored"])
        # No oracle ran on the aborted attempt, so it must not claim a measurement.
        self.assertIsNone(log[-1]["degraded"])
        # Never re-injected on a state nobody can describe.
        self.assertNotIn("inject_fault", platform.actions)

    def test_a_failed_reinjection_ends_the_loop_at_once(self) -> None:
        platform = RecordingPlatform(reinjects=[{"ok": False, "error": "device unreachable"}])
        evaluation, log, platform = _run([False], attempts=3, platform=platform)
        self.assertFalse(evaluation.passed)
        self.assertEqual(2, len(log))
        self.assertIs(False, log[-1]["reinjected"])
        self.assertIsNone(log[-1]["degraded"])

    def test_a_fault_with_no_restore_commands_measures_again_in_place(self) -> None:
        platform = RecordingPlatform(restore={"ok": True, "skipped": True})
        evaluation, log, platform = _run([False, True], attempts=3, platform=platform)
        self.assertTrue(evaluation.passed)
        # Nothing to put back means nothing to inject a second time; the retry is
        # the fresh measurement itself.
        self.assertEqual(["restore_fault"], platform.actions)
        self.assertIs(False, log[-1]["reinjected"])

    def test_one_attempt_is_the_floor(self) -> None:
        _, log, platform = _run([False], attempts=0)
        self.assertEqual(1, len(log))
        self.assertEqual([], platform.actions)

    def test_attempts_made_before_the_evaluator_raised_stay_in_the_callers_log(self) -> None:
        platform = RecordingPlatform()
        evaluator = ScriptedEvaluator([False, RuntimeError("probe host unreachable")])
        log: list[dict[str, Any]] = []
        with self.assertRaises(RuntimeError):
            _inject_until_degraded(
                _Deps(platform, evaluator), _scenario(), {"phase": "expected_degradation"}, {},
                attempts=3, log=log,
            )
        # The first draw measured; the exception on the second must not erase it.
        self.assertEqual(
            [{"attempt": 1, "restored": None, "reinjected": None, "degraded": False,
              "voided": False}], log
        )
        self.assertEqual(["restore_fault", "inject_fault"], platform.actions)


class RetryErrorMessageTests(unittest.TestCase):
    """A restore that failed and a fault that never showed must not read alike."""

    def _failed(self) -> OracleEvaluation:
        return OracleEvaluation(
            oracle_id="qos.test.expected_degradation", version="1.0.0",
            phase="expected_degradation", passed=False, probes=(),
        )

    def test_an_undetected_fault_names_the_attempt_count(self) -> None:
        log = [{"attempt": n, "restored": True, "reinjected": True, "degraded": False}
               for n in (1, 2, 3)]
        with self.assertRaises(LifecycleError) as caught:
            _require_oracle(self._failed(), attempts=log)
        self.assertIn("after 3 injection attempt(s)", str(caught.exception))

    def test_a_failed_restore_says_so_instead(self) -> None:
        log = [
            {"attempt": 1, "restored": None, "reinjected": None, "degraded": False},
            {"attempt": 2, "restored": False, "reinjected": None, "degraded": None},
        ]
        with self.assertRaises(LifecycleError) as caught:
            _require_oracle(self._failed(), attempts=log)
        message = str(caught.exception)
        self.assertIn("reference restore failed before injection attempt 2", message)
        self.assertNotIn("injection attempt(s)", message.split(";")[0])

    def test_a_failed_reinjection_says_so_instead(self) -> None:
        log = [
            {"attempt": 1, "restored": None, "reinjected": None, "degraded": False},
            {"attempt": 2, "restored": True, "reinjected": False, "degraded": None},
        ]
        with self.assertRaises(LifecycleError) as caught:
            _require_oracle(self._failed(), attempts=log)
        self.assertIn("re-injection failed on attempt 2", str(caught.exception))

    def test_a_passing_oracle_raises_nothing(self) -> None:
        passing = OracleEvaluation(
            oracle_id="qos.test.expected_degradation", version="1.0.0",
            phase="expected_degradation", passed=True, probes=(),
        )
        _require_oracle(passing, attempts=[{"attempt": 1, "degraded": True}])


if __name__ == "__main__":
    unittest.main()
