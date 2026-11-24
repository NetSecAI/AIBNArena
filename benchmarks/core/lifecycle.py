"""The single fourteen-phase experimental lifecycle."""
from __future__ import annotations

import difflib
import gzip
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from scenarios.oracle_loader import resolve_oracle

from .contracts import (
    BenchmarkPlatform,
    ExperimentConfig,
    OperationCounts,
    OracleEvaluation,
    ResultSink,
    SUTClient,
    SUTResponse,
    ScenarioDefinition,
)
from .diagnosis import assess_diagnosis, reference_for_scenario
from .metrics import calculate_shared_metrics, repair_without_change
from .oracle_evaluator import OracleEvaluator
from .provenance import collect_provenance
from .tracing import TraceRecorder, TraceSink


ConfigLoader = Callable[[str | Path], ExperimentConfig]
ScenarioLoader = Callable[[str | Path], ScenarioDefinition]
OracleLoader = Callable[[str | Path, str], Mapping[str, Any]]
DomainMetrics = Callable[[Mapping[str, OracleEvaluation], Mapping[str, Any]], Mapping[str, Any]]


class LifecycleError(RuntimeError):
    """A lifecycle invariant failed, optionally after a result was persisted."""

    def __init__(self, message: str, *, result_path: Path | None = None):
        super().__init__(message)
        self.result_path = result_path


@dataclass(frozen=True)
class LifecycleDependencies:
    config_loader: ConfigLoader
    scenario_loader: ScenarioLoader
    oracle_loader: OracleLoader
    platform: BenchmarkPlatform
    evaluator: OracleEvaluator
    sut_client: SUTClient
    result_sink: ResultSink
    repository: Path
    domain_metrics: DomainMetrics | None = None
    trace_sink: TraceSink | None = None
    #: Scores the subject's stated diagnosis against the injected fault (evaluation
    #: parameter 13); anything with the `score(reference, hypothesis)` and
    #: `identity()` of `benchmarks.core.diagnosis.PLUIEJudge`. None records the
    #: diagnosis unjudged.
    diagnosis_judge: Any | None = None


class BenchmarkLifecycle:
    """Run all benchmark domains through the same ordered lifecycle."""

    PHASES = (
        "load_configuration",
        "load_validate_scenario",
        "load_oracles",
        "deploy_reset_testbed",
        "apply_reference_state",
        "evaluate_healthy",
        "record_baseline",
        "snapshot_healthy",
        "inject_fault",
        "evaluate_degradation",
        "prime_before_sut",
        "snapshot_before_sut",
        "invoke_sut",
        "snapshot_after_sut",
        "evaluate_repair_preservation",
        "snapshot_post_repair",
        "calculate_metrics",
        "score_diagnosis",
        "restore_destroy",
        "write_result",
    )

    def __init__(self, dependencies: LifecycleDependencies):
        self.dependencies = dependencies

    def run(self, config_path: str | Path) -> Path:
        deps = self.dependencies
        trace = TraceRecorder(deps.trace_sink)
        evaluations: dict[str, OracleEvaluation] = {}
        operations = OperationCounts()
        output_path: Path | None = None
        cleanup_result: Mapping[str, Any] = {}
        convergence_result: Mapping[str, Any] = {}
        metrics: dict[str, Any] = {"success": False}
        failure: Exception | None = None
        failure_phase: str | None = None
        sut_task_id = f"sut-{secrets.token_hex(16)}"
        # Hoisted out of the try below: _build_result reads this name after the
        # try/finally on every path, so a run that fails before the degradation
        # phase (deployment, reference state, healthy oracle) would otherwise die
        # with UnboundLocalError there, outside the try, and write no result at all.
        injection_attempts: list[dict[str, Any]] = []
        # Hoisted for the same reason: what the priming before the subject did, null when
        # the platform cannot prime or the run failed before it.
        priming_before_sut: dict[str, Any] | None = None

        with trace.phase("load_configuration"):
            config = deps.config_loader(config_path)
        response = SUTResponse(
            result={},
            identity="unavailable",
            version="unavailable",
        )
        with trace.phase("load_validate_scenario"):
            scenario = deps.scenario_loader(config.scenario_path)
        with trace.phase("load_oracles"):
            oracles = {
                phase: resolve_oracle(
                    deps.oracle_loader(reference.path, reference.version),
                    scenario.bindings,
                )
                for phase, reference in scenario.oracles.items()
            }
            for phase, oracle in oracles.items():
                if oracle["phase"] != phase:
                    raise LifecycleError(
                        f"oracle {oracle['oracle_id']} declares {oracle['phase']}, expected {phase}"
                    )
                if oracle["domain"] != scenario.domain:
                    raise LifecycleError(
                        f"oracle {oracle['oracle_id']} domain does not match {scenario.domain}"
                    )

        # Per-device configuration at three moments, as files beside the record. The
        # directory is named before the run id exists; the record points at it.
        snapshots = DeviceSnapshots(
            deps.platform,
            Path(config.result_dir) / "devices"
            / f"{scenario.scenario_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{sut_task_id[-8:]}",
        )
        try:
            with trace.phase("deploy_reset_testbed"):
                _require_ok(deps.platform.deploy_or_reset(config.testbed), "testbed deployment/reset")
            with trace.phase("apply_reference_state"):
                _require_ok(
                    deps.platform.apply_reference_state(scenario.reference_state),
                    "reference-state application",
                )
            with trace.phase("evaluate_healthy"):
                evaluations["healthy"] = deps.evaluator.evaluate(oracles["healthy"])
                operations.validations += 1
                _require_oracle(evaluations["healthy"])
            with trace.phase("record_baseline"):
                baseline = {
                    probe.probe_id: dict(probe.metrics)
                    for probe in evaluations["healthy"].probes
                }
            with trace.phase("snapshot_healthy"):
                snapshots.take("healthy")
            # A no-fault episode keeps the lab healthy: the two injection phases
            # are not entered, their durations read 0.0, and the subject is handed
            # the task with nothing to repair.
            if scenario.fault_applicable:
                with trace.phase("inject_fault"):
                    _require_ok(deps.platform.inject_fault(scenario.fault), "fault injection")
                with trace.phase("evaluate_degradation"):
                    try:
                        evaluation, _ = _inject_until_degraded(
                            deps,
                            scenario,
                            oracles["expected_degradation"],
                            baseline,
                            attempts=config.injection_attempts,
                            log=injection_attempts,
                        )
                    finally:
                        # Every draw that measured is a validation, including the ones
                        # made before a later draw raised. An attempt that aborted
                        # before measuring (restore or re-injection failed) ran no
                        # oracle, so it is not one.
                        operations.validations += sum(
                            1 for attempt in injection_attempts if attempt.get("degraded") is not None
                        )
                    evaluations["expected_degradation"] = evaluation
                    _require_oracle(evaluation, attempts=injection_attempts)
            # The network the subject is handed has fresh learned tables. A subject that
            # places a Linux node from the ARP or MAC entry a neighbour holds for it finds
            # none once the kernel drops an entry unused for 60 s, which it does when the
            # host's ARP table, shared by every lab copy on the machine, holds 128 entries
            # (`gc_thresh1`; checked on a two-node VyOS lab on 2026-11-16). Priming is
            # traffic, so it comes after the last measurement before the subject, and it
            # changes no configuration; the before_sut read then sees what the subject sees.
            with trace.phase("prime_before_sut"):
                priming_before_sut = prime_learned_tables(deps.platform)
            with trace.phase("snapshot_before_sut"):
                snapshots.take("before_sut")
            try:
                with trace.phase("invoke_sut"):
                    response = deps.sut_client.invoke(
                        {
                            "experiment_id": config.experiment_id,
                            "scenario_id": sut_task_id,
                            "success_criteria": scenario.success_criteria,
                            "domain": scenario.domain,
                            "intent": scenario.intent,
                            "observation": public_observation(
                                scenario.success_criteria, scenario.bindings,
                                repair_throughput_reference(oracles.get("repair"), baseline)),
                        }
                    )
                    operations.merge(response.operations)
            finally:
                # Also when the answer failed (a client timeout, a malformed report):
                # the subject may have changed devices before it, and what it left is
                # the evidence the record would otherwise lack.
                with trace.phase("snapshot_after_sut"):
                    snapshots.take("after_sut")
            with trace.phase("evaluate_repair_preservation"):
                convergence_result = deps.platform.wait_for_convergence()
                # Non-convergence is scoring evidence, not a lifecycle error. The
                # independent repair oracle below still records the final state.
                evaluations["repair"] = deps.evaluator.evaluate(
                    oracles["repair"], baseline=baseline
                )
                operations.validations += 1
                if scenario.preservation_applicable:
                    evaluations["preservation"] = deps.evaluator.evaluate(
                        oracles["preservation"], baseline=baseline
                    )
                    operations.validations += 1
            # After the oracles: its priming is traffic, and no measurement may see it.
            with trace.phase("snapshot_post_repair"):
                snapshots.take_post_repair()
            with trace.phase("calculate_metrics"):
                metrics = calculate_shared_metrics(
                    evaluations, operations, fault_applicable=scenario.fault_applicable)
                metrics["environment_success"] = metrics["success"]
                metrics["converged"] = convergence_result.get("converged") is True
                metrics["sut_completed"] = response.result.get("status") == "completed"
                metrics["sut_verified"] = response.result.get("verified") is True
                # The scenario's verdict is the oracle's alone (2026-11-21, Hugo). Convergence
                # and the subject's own end and check stay recorded beside it, not part of it:
                # until then success also required all three.
                metrics["success"] = metrics["environment_success"]
                # Audit, not verdict: a repair the oracle passed on a lab the subject
                # did not touch is not the subject's repair (metrics.repair_without_change).
                metrics["repair_without_change"] = repair_without_change(
                    evaluations.get("repair"),
                    snapshots.changed_between("before_sut", "after_sut"),
                    fault_applicable=scenario.fault_applicable,
                )

                if deps.domain_metrics is not None:
                    metrics.update(deps.domain_metrics(evaluations, response.result))
            with trace.phase("score_diagnosis"):
                # Never raises: a judge that cannot be reached is written into the
                # block as the reason the episode went unjudged, and the repair
                # verdict above stands whatever happened here.
                # A change request injects nothing, so there is no fault to have found:
                # the block says so instead of scoring the subject against a request.
                metrics["diagnosis"] = assess_diagnosis(
                    reference_for_scenario(scenario), response.result, deps.diagnosis_judge,
                    fault_applicable=(scenario.fault_applicable
                                      and not bool((scenario.fault or {}).get("change_request"))))
        except Exception as exc:
            failure = exc
            failure_phase = trace.failed_phase or trace.active_phase or "unknown"
        finally:
            try:
                with trace.phase("restore_destroy"):
                    cleanup_result = deps.platform.restore_or_destroy(config.cleanup)
                    _require_ok(cleanup_result, "testbed cleanup")
            except Exception as cleanup_exc:
                if not cleanup_result:
                    cleanup_result = {"ok": False, "error": str(cleanup_exc)}
                if failure is None:
                    failure = cleanup_exc
                    failure_phase = "restore_destroy"

        result = self._build_result(
            config,
            scenario,
            sut_task_id,
            oracles,
            evaluations,
            injection_attempts,
            operations,
            response,
            metrics,
            cleanup_result,
            convergence_result,
            failure,
            failure_phase,
            trace,
            device_configurations=snapshots.manifest(),
            priming_before_sut=priming_before_sut,
        )
        with trace.phase("write_result"):
            output_path = deps.result_sink.write(result, config.result_dir)

        # Refresh the duration of the write phase atomically without creating a
        # fifteenth lifecycle phase.
        result["phase_durations"] = {
            phase: trace.phase_durations.get(phase, 0.0) for phase in self.PHASES
        }
        output_path = deps.result_sink.write(result, config.result_dir)
        if failure is not None:
            raise LifecycleError(
                f"benchmark failed during {failure_phase}: {failure}; result written to {output_path}",
                result_path=output_path,
            ) from failure
        return output_path

    def _build_result(
        self,
        config: ExperimentConfig,
        scenario: ScenarioDefinition,
        sut_task_id: str,
        oracles: Mapping[str, Mapping[str, Any]],
        evaluations: Mapping[str, OracleEvaluation],
        injection_attempts: list[dict[str, Any]],
        operations: OperationCounts,
        response: Any,
        metrics: Mapping[str, Any],
        cleanup_result: Mapping[str, Any],
        convergence_result: Mapping[str, Any],
        failure: Exception | None,
        failure_phase: str | None,
        trace: TraceRecorder,
        *,
        device_configurations: Mapping[str, Any] | None = None,
        priming_before_sut: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        durations = {phase: trace.phase_durations.get(phase, 0.0) for phase in self.PHASES}
        evaluation_payloads = {
            phase: evaluation.as_dict() for phase, evaluation in evaluations.items()
        }
        metrics_payload = dict(metrics)
        injection = {
            "attempts_allowed": config.injection_attempts,
            "attempts": [dict(attempt) for attempt in injection_attempts],
        }
        # The attempt log rides on the degradation evaluation it explains. The result
        # root admits no extra keys, while evaluation payloads are open objects.
        if "expected_degradation" in evaluation_payloads:
            evaluation_payloads["expected_degradation"]["injection"] = injection
        elif injection_attempts:
            # The evaluator raised mid-attempt, so no degradation evaluation exists
            # to carry the log; metrics is the other open object under the root.
            metrics_payload["injection"] = injection
        return {
            "schema_version": "1.0",
            "run_id": f"{config.experiment_id}-{uuid.uuid4().hex[:12]}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed" if failure is not None else "completed",
            "experiment_id": config.experiment_id,
            "scenario": {
                "id": scenario.scenario_id,
                "version": scenario.version,
                "domain": scenario.domain,
                "sut_task_id": sut_task_id,
            },
            "provenance": collect_provenance(
                config,
                scenario,
                oracles,
                response,
                repository=self.dependencies.repository,
            ),
            "operation_counts": operations.as_dict(),
            "phase_durations": durations,
            "evaluations": evaluation_payloads,
            "metrics": metrics_payload,
            "sut_result": dict(response.result),
            "cleanup_result": dict(cleanup_result),
            "convergence_result": dict(convergence_result),
            # Where the per-device configuration files are and what changed between
            # them; null when the platform cannot read device configuration.
            "device_configurations": (
                dict(device_configurations) if device_configurations is not None else None
            ),
            # What the priming before the subject did (`prime_before_sut`): the platform's
            # counts, or its error; null when the platform cannot prime or the run ended
            # before it.
            "priming_before_sut": (
                dict(priming_before_sut) if priming_before_sut is not None else None
            ),
            "error": (
                {
                    "phase": failure_phase or "unknown",
                    "type": type(failure).__name__,
                    "message": str(failure),
                }
                if failure is not None
                else None
            ),
        }


class DeviceSnapshots:
    """The running configuration of every device at three moments, as files beside
    the record, with the diffs between them: `healthy` (after the reference state),
    `before_sut` (the faulty network the subject was handed) and `after_sut` (what
    it left, before convergence). Hugo asked for the per-device configuration before
    and after the subject's modification (2026-09-16).

    The judge also keeps every device as `get_running_config(format="json")` answers it
    (`collect_structured_device_data`), one compressed file per device beside its text,
    so that the network can be analysed after the run, without a lab. It is taken
    before and after the subject, and once more at the end of the episode
    (`take_post_repair`). Hugo asked for it on 2026-10-27 for the LangChain subjects and
    on 2026-11-10 for every subject.

    A platform without `collect_device_configurations` records nothing (the record
    says null). A snapshot that fails records its error and the episode goes on: this
    is evidence for the reader, not a lifecycle requirement.
    """

    LABELS = ("healthy", "before_sut", "after_sut")
    DIFFS = (("healthy", "before_sut"), ("before_sut", "after_sut"))
    #: The moments the structured read is kept: the network the subject was handed and
    #: the one it left. Not `healthy`: 600 kB a moment on sme01-small before compression
    #: (96 kB after), and the campaign records that carry these files are kept in git.
    STRUCTURED_LABELS = ("before_sut", "after_sut")
    #: The episode's last structured read: the network the subject left, once the
    #: episode is over.
    POST_REPAIR = "post_repair"

    def __init__(self, platform: Any, directory: Path, *, structured: bool = True) -> None:
        self._collect = getattr(platform, "collect_device_configurations", None)
        self._collect_structured = getattr(platform, "collect_structured_device_data", None) if structured else None
        self._prime = getattr(platform, "prime_learned_tables", None) if structured else None
        self.directory = Path(directory)
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.structured: dict[str, dict[str, Any]] = {}
        self._texts: dict[str, dict[str, tuple[str, str]]] = {}

    @property
    def available(self) -> bool:
        return callable(self._collect)

    def take(self, label: str) -> None:
        if not self.available:
            return
        self._take_configuration(label)
        if label in self.STRUCTURED_LABELS and callable(self._collect_structured):
            self._take_structured(label)

    def take_post_repair(self) -> None:
        """`<device>/post_repair.json.gz`: the structured read of what the episode ended
        with, once the network has converged and the oracles have measured it. The Linux
        nodes send a frame first, so that the switches' learnt MAC addresses place them:
        `after_sut`, which nothing primes, misses some of them. A priming that fails is
        recorded and the read goes on."""
        if not self.available or not callable(self._collect_structured):
            return
        try:
            priming = dict(self._prime()) if callable(self._prime) else None
        except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
            priming = {"error": f"{type(exc).__name__}: {exc}"}
        self._take_structured(self.POST_REPAIR)
        self.structured[self.POST_REPAIR]["priming"] = priming

    def _take_configuration(self, label: str) -> None:
        try:
            collected = self._collect()
            devices = collected.get("devices") if isinstance(collected, Mapping) else None
            if not isinstance(devices, Mapping):
                error = (collected.get("error") if isinstance(collected, Mapping) else None) or "platform returned no devices"
                self.snapshots[label] = {"ok": False, "error": str(error), "devices": {}}
                return
            entry: dict[str, Any] = {"ok": bool(collected.get("ok")), "devices": {}}
            texts: dict[str, tuple[str, str]] = {}
            for name, device in devices.items():
                device = device if isinstance(device, Mapping) else {}
                text = str(device.get("configuration") or "")
                path = self.directory / str(name) / f"{label}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                texts[str(name)] = (str(device.get("kind") or ""), text)
                record = {"kind": device.get("kind"), "ok": device.get("ok") is True, "command": device.get("command"),
                          "file": str(path.relative_to(self.directory)), "bytes": len(text.encode("utf-8"))}
                if device.get("ok") is not True:
                    record["error"] = str(device.get("error") or device.get("stderr") or "read failed")
                entry["devices"][str(name)] = record
            self._texts[label] = texts
            self.snapshots[label] = entry
        except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
            self.snapshots[label] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "devices": {}}

    def _take_structured(self, label: str) -> None:
        """`<device>/<label>.json.gz`: the device's `get_running_config(format="json")`
        record, with its container and roles, gzipped (SR Linux state alone is a few
        hundred kilobytes a switch). Written for a device whose read failed too: its
        error is the evidence."""
        started = time.monotonic()
        try:
            collected = self._collect_structured()
            devices = collected.get("devices") if isinstance(collected, Mapping) else None
            if not isinstance(devices, Mapping):
                error = (collected.get("error") if isinstance(collected, Mapping) else None) or "platform returned no devices"
                self.structured[label] = {"ok": False, "error": str(error), "devices": {}}
                return
            entry: dict[str, Any] = {"ok": collected.get("ok") is True, "devices": {}}
            for name, record in devices.items():
                record = dict(record) if isinstance(record, Mapping) else {"ok": False, "error": "malformed record"}
                data = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
                path = self.directory / str(name) / f"{label}.json.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                # mtime=0: the same read gives the same bytes.
                path.write_bytes(gzip.compress(data, mtime=0))
                summary = {"kind": record.get("kind"), "ok": record.get("ok") is True,
                           "roles": record.get("roles"), "file": str(path.relative_to(self.directory)),
                           "bytes": len(data)}
                if record.get("ok") is not True:
                    summary["error"] = str(record.get("error") or "read failed")
                entry["devices"][str(name)] = summary
            entry["seconds"] = round(time.monotonic() - started, 3)
            self.structured[label] = entry
        except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
            self.structured[label] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "devices": {}}

    def _compared(self, before: str, after: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """(device, lines before, lines after) for every device either snapshot read."""
        for name in sorted(set(self._texts[before]) | set(self._texts[after])):
            kind, old = self._texts[before].get(name, ("", ""))
            kind_after, new = self._texts[after].get(name, (kind, ""))
            kind = kind or kind_after
            yield name, _stable_lines(kind, old), _stable_lines(kind, new)

    def changed_between(self, before: str, after: str) -> list[str] | None:
        """The devices whose configuration differs between two snapshots, written
        nowhere. None when either snapshot is missing or one of its reads failed: an
        unread device compares equal to itself and would pass for unchanged."""
        if any((self.snapshots.get(label) or {}).get("ok") is not True or label not in self._texts
               for label in (before, after)):
            return None
        return [name for name, old, new in self._compared(before, after) if old != new]

    def diffs(self) -> dict[str, dict[str, Any]]:
        """Unified diffs per device between consecutive snapshots, one file each,
        only where the configuration differs."""
        changes: dict[str, dict[str, Any]] = {}
        for before, after in self.DIFFS:
            if before not in self._texts or after not in self._texts:
                continue
            key = f"{before}_to_{after}"
            changes[key] = {}
            for name, old_lines, new_lines in self._compared(before, after):
                if old_lines == new_lines:
                    continue
                diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"{name}/{before}.txt",
                                                 tofile=f"{name}/{after}.txt", lineterm=""))
                path = self.directory / name / f"{key}.diff"
                path.write_text("\n".join(diff) + "\n", encoding="utf-8")
                changes[key][name] = {
                    "lines_added": sum(1 for line in diff[2:] if line.startswith("+")),
                    "lines_removed": sum(1 for line in diff[2:] if line.startswith("-")),
                    "file": str(path.relative_to(self.directory)),
                }
        return changes

    def manifest(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        manifest: dict[str, Any] = {
            "directory": str(self.directory),
            "snapshots": self.snapshots,
            "changes": {},
            "changed_by_sut": [],
            "structured": self.structured,
            "note": ("running configuration per device: healthy (after the reference state), before_sut "
                     "(the faulty network the subject was handed), after_sut (what it left, before "
                     "convergence); diffs ignore Linux address lifetimes; structured: the "
                     "get_running_config(format=json) record of every device at before_sut, after_sut "
                     "and post_repair (after convergence, the oracles and priming), gzipped"),
        }
        try:
            manifest["changes"] = self.diffs()
            manifest["changed_by_sut"] = sorted(manifest["changes"].get("before_sut_to_after_sut") or {})
            if self.snapshots:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            manifest["error"] = f"{type(exc).__name__}: {exc}"
        return manifest


def _stable_lines(kind: str, text: str) -> list[str]:
    """The lines a diff compares. Linux `ip address show` prints address lifetimes
    that tick on every read (DHCP leases); they are not configuration."""
    lines = [line.rstrip() for line in text.splitlines()]
    if kind == "linux":
        lines = [line for line in lines if "valid_lft" not in line]
    return lines


def prime_learned_tables(platform: Any) -> dict[str, Any] | None:
    """Have the platform make its Linux nodes send a frame (`prime_learned_tables`), and
    say what it did: its counts, `{"error": ...}` when it raised, None when it cannot.
    Never raises: the subject is handed its task whatever happened here."""
    prime = getattr(platform, "prime_learned_tables", None)
    if not callable(prime):
        return None
    try:
        return dict(prime())
    except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
        return {"error": f"{type(exc).__name__}: {exc}"}


def _require_ok(outcome: Mapping[str, Any], operation: str) -> None:
    if outcome.get("ok") is not True:
        raise LifecycleError(f"{operation} failed: {outcome.get('error', 'unknown error')}")


def _voided(evaluation: OracleEvaluation) -> bool:
    """Whether a draw was thrown out by its instrument rather than judged.

    A throughput probe whose offered contention was not observed reports no
    throughput at all; the draw then fails, but for a reason that says nothing
    about the fault. The attempt log keeps the two apart so a retry that later
    passed does not read as "the fault was not seen on the first draw".
    """
    return any(
        probe.metrics.get("contention_observed") is False
        for probe in evaluation.probes
    )


def _inject_until_degraded(
    deps: "LifecycleDependencies",
    scenario: ScenarioDefinition,
    oracle: Mapping[str, Any],
    baseline: Mapping[str, Mapping[str, Any]],
    *,
    attempts: int,
    log: list[dict[str, Any]],
) -> tuple[OracleEvaluation, list[dict[str, Any]]]:
    """Evaluate the degradation, re-injecting while it goes unseen.

    On a dual-homed edge the measured flow's path is a per-flow hash, so one draw
    that did not see the fault is not evidence the fault is absent. A retry first
    replays the fault's own restore commands, then injects again, then evaluates a
    fresh measurement. A restore that fails ends the loop at once: stacking a second
    injection on an unknown state would measure nothing anyone promised, and the
    caller has to be able to tell that apart from a fault that simply never showed.

    The fault is already in place when this is called, so the first pass only
    evaluates. Each attempt is appended to the caller's `log` as it happens, so the
    attempts already made survive when a later draw raises; the same list is also
    returned with the last evaluation. An attempt that aborted before measuring
    records "degraded": None, since no evaluation ran and False would claim a
    measurement that never happened.
    """
    evaluation = deps.evaluator.evaluate(oracle, baseline=baseline)
    log.append({"attempt": 1, "restored": None, "reinjected": None,
                "degraded": evaluation.passed, "voided": _voided(evaluation)})
    if evaluation.passed:
        return evaluation, log

    for attempt in range(2, max(1, int(attempts)) + 1):
        restored = deps.platform.restore_fault()
        if not restored.get("ok"):
            log.append({
                "attempt": attempt, "restored": False, "reinjected": None, "degraded": None,
                "error": restored.get("error"),
            })
            break
        # A fault that declares no restore commands cannot be put back, so it cannot
        # be injected a second time either: this attempt measures again on the state
        # already in place, which is still a fresh flow and a fresh draw.
        reinjectable = not restored.get("skipped")
        if reinjectable:
            reinjected = deps.platform.inject_fault(scenario.fault)
            if not reinjected.get("ok"):
                log.append({
                    "attempt": attempt, "restored": True, "reinjected": False, "degraded": None,
                    "error": reinjected.get("error"),
                })
                break
        evaluation = deps.evaluator.evaluate(oracle, baseline=baseline)
        log.append({"attempt": attempt, "restored": True, "reinjected": reinjectable,
                    "degraded": evaluation.passed, "voided": _voided(evaluation)})
        if evaluation.passed:
            break
    return evaluation, log


def _require_oracle(
    evaluation: OracleEvaluation,
    *,
    attempts: list[dict[str, Any]] | None = None,
) -> None:
    if evaluation.passed:
        return
    detail = f"{evaluation.phase} oracle {evaluation.oracle_id}@{evaluation.version} failed"
    if attempts:
        last = attempts[-1]
        # A run that could not put the reference back is not the same finding as one
        # where the fault genuinely never showed, and the operator has to be able to
        # tell them apart from the message alone.
        if last.get("restored") is False:
            detail = (
                f"reference restore failed before injection attempt {last['attempt']}; "
                f"{len(attempts)} attempt(s) did not observe the expected degradation"
            )
        elif last.get("reinjected") is False:
            detail = (
                f"re-injection failed on attempt {last['attempt']}; "
                f"{len(attempts)} attempt(s) did not observe the expected degradation"
            )
        else:
            detail = (
                f"{detail} after {len(attempts)} injection attempt(s)"
            )
    raise LifecycleError(detail)


def repair_throughput_reference(
    repair_oracle: Mapping[str, Any] | None, baseline: Mapping[str, Mapping[str, Any]],
) -> float | None:
    """The healthy rate the repair oracle holds the protected flow to, or None.

    The shaped families' repair oracles ask the flow for the lower of its contract and what
    the healthy network of the same episode measured (`baseline` with `combine: min`), each
    with the same tolerance. This is that measurement: the healthy probe's reading of the
    metric the oracle's baseline-relative throughput threshold names.
    """
    for probe in (repair_oracle or {}).get("probes") or []:
        for threshold in probe.get("thresholds") or []:
            relative = threshold.get("baseline")
            if threshold.get("metric") != "throughput_mbps" or not isinstance(relative, Mapping):
                continue
            value = (baseline.get(str(probe.get("id"))) or {}).get(str(relative.get("metric")))
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
    return None


def public_service_measurement(
    success_criteria: Mapping[str, Any] | None, bindings: Mapping[str, Any],
    healthy_throughput_mbps: float | None = None,
) -> dict[str, Any] | None:
    """The flow the subject's own throughput check measures, or None when it has none.

    `observed_throughput` in the public success criteria is judged by the ANI with the
    same instrument the runner uses, and the ANI reads the flow from the task's
    `observation.service_measurement` (source, destination, destination_ip; the sink
    ports stay in the topology and are never published). Nothing produced that entry
    before 2026-09-16, so every qos episode of E1 and the first E3 qos episodes saw
    "missing public service measurement", could not pass their own validation and
    could not conclude "completed" however well they had repaired the link.

    `healthy_throughput_mbps` is what the flow measured on the healthy network of the
    episode (`repair_throughput_reference`), with which the ANI's check holds the flow to
    the repair oracle's bar. Until 2026-11-15 the check asked for the contract alone: in
    main03, 14 of the 24 healthy networks of campus_large read below it (5.96 to 6.28
    against 6.8 Mbit/s), and a repair putting back exactly the healthy shaper could pass
    the subject's own check there only on a luckier sample, while a success needs it.
    """
    criteria = (success_criteria or {}).get("all_of") if isinstance(success_criteria, Mapping) else None
    if not isinstance(criteria, list) or not any(
            isinstance(item, Mapping) and item.get("type") == "observed_throughput" for item in criteria):
        return None
    source = bindings.get("protected_source") or bindings.get("source")
    destination = bindings.get("destination")
    destination_ip = bindings.get("destination_ip")
    if not (source and destination and destination_ip):
        return None
    service = {"source": str(source), "destination": str(destination), "destination_ip": str(destination_ip)}
    if healthy_throughput_mbps is not None:
        service["healthy_throughput_mbps"] = round(float(healthy_throughput_mbps), 3)
    return service


#: The paths a `segment_isolation` criterion measures, as the compiler bound them.
SEGMENT_ISOLATION_PATHS = ("reachable_paths", "same_segment_paths", "isolated_paths", "preserved_paths")


def public_segment_isolation(
    success_criteria: Mapping[str, Any] | None, bindings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The paths the subject's own `segment_isolation` check measures, or None when the
    criteria declare none.

    The ANI reads them from the task's `observation.segment_isolation`, as it reads the
    measured flow of a throughput criterion from `service_measurement`: the members of
    the new VLAN, which pairs must answer, which must answer without crossing a router,
    which must not answer at all, and which paths of the rest of the network must keep
    working. All of it follows from the task's own wording; none of it says how to
    build the VLAN.
    """
    criteria = (success_criteria or {}).get("all_of") if isinstance(success_criteria, Mapping) else None
    if not isinstance(criteria, list) or not any(
            isinstance(item, Mapping) and item.get("type") == "segment_isolation" for item in criteria):
        return None
    if not bindings.get("members"):
        return None
    return {
        "members": [str(node) for node in bindings["members"]],
        **{key: [str(path) for path in bindings.get(key) or []] for key in SEGMENT_ISOLATION_PATHS},
    }


def public_service_publication(
    success_criteria: Mapping[str, Any] | None, bindings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """What the subject's own `service_publication` check measures, or None when the criteria
    declare none: the client outside, the service host and port it must reach, and the hosts
    of the service's zone that must answer it on nothing else. All of it follows from the
    task's wording; none of it says which ruleset to write."""
    criteria = (success_criteria or {}).get("all_of") if isinstance(success_criteria, Mapping) else None
    if not isinstance(criteria, list) or not any(
            isinstance(item, Mapping) and item.get("type") == "service_publication" for item in criteria):
        return None
    if not (bindings.get("external_source") and bindings.get("service_node") and bindings.get("service_port")):
        return None
    return {
        "source": str(bindings["external_source"]),
        "service": str(bindings["service_node"]),
        "port": int(bindings["service_port"]),
        "transport": str(bindings.get("service_transport") or "tcp"),
        "closed_hosts": [str(node) for node in bindings.get("closed_hosts") or []],
    }


def public_observation(
    success_criteria: Mapping[str, Any] | None, bindings: Mapping[str, Any],
    healthy_throughput_mbps: float | None = None,
) -> dict[str, Any] | None:
    """The observation the request carries: the measured flow when the criteria need one,
    what a change request is measured on when they declare one."""
    observation: dict[str, Any] = {}
    service = public_service_measurement(success_criteria, bindings, healthy_throughput_mbps)
    if service:
        observation["service_measurement"] = service
    isolation = public_segment_isolation(success_criteria, bindings)
    if isolation:
        observation["segment_isolation"] = isolation
    publication = public_service_publication(success_criteria, bindings)
    if publication:
        observation["service_publication"] = publication
    return observation or None
