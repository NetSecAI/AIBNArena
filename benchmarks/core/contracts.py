"""Framework-neutral contracts shared by benchmark domains and platforms."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


ORACLE_PHASES = ("healthy", "expected_degradation", "repair", "preservation")


@dataclass(frozen=True)
class OracleReference:
    path: str
    version: str

    @classmethod
    def from_value(cls, value: Mapping[str, Any], *, phase: str) -> "OracleReference":
        path = str(value.get("path", "")).strip()
        version = str(value.get("version", "")).strip()
        if not path or not version:
            raise ValueError(f"{phase} oracle requires path and version")
        return cls(path=path, version=version)


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    version: str
    domain: str
    intent: str
    topology: str
    reference_state: str
    fault: Mapping[str, Any]
    bindings: Mapping[str, Any]
    oracles: Mapping[str, OracleReference]
    preservation_applicable: bool = True
    success_criteria: Mapping[str, Any] | None = None
    #: Which wording of the task the subject was given, when the scenario declares
    #: more than one. None when it states a single task.
    intent_variant: str | None = None
    seed: int = 0
    #: Whether the compiled fault is injected. False runs the same instance on a
    #: healthy lab: the subject is handed the task with nothing to repair, and any
    #: mutation it asks the ANI for is the false positive the episode measures.
    #: The degradation oracle has nothing to measure then and is not loaded.
    fault_applicable: bool = True
    #: The scenario method the compiler selected (`name`, `selector`, `operation`),
    #: which with `bindings` says in words what was injected. It is what the
    #: diagnosis judge compares the subject's own statement of the fault against
    #: (benchmarks/core/diagnosis.py). None where a scenario names no method.
    method: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScenarioDefinition":
        required = ("id", "version", "domain", "intent", "topology", "reference_state", "fault", "bindings", "oracles")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"scenario missing required fields: {', '.join(missing)}")
        raw_oracles = value["oracles"]
        if not isinstance(raw_oracles, Mapping):
            raise ValueError("scenario oracles must be a mapping")
        applicable = bool(value.get("preservation_applicable", True))
        fault_applicable = bool(value.get("fault_applicable", True))
        required_phases = list(ORACLE_PHASES if applicable else ORACLE_PHASES[:-1])
        if not fault_applicable:
            required_phases = [phase for phase in required_phases if phase != "expected_degradation"]
        missing_phases = [phase for phase in required_phases if phase not in raw_oracles]
        if missing_phases:
            raise ValueError(f"scenario missing explicit oracle references: {', '.join(missing_phases)}")
        oracles = {
            phase: OracleReference.from_value(raw_oracles[phase], phase=phase)
            for phase in required_phases
        }
        return cls(
            scenario_id=str(value["id"]),
            version=str(value["version"]),
            domain=str(value["domain"]),
            intent=str(value["intent"]),
            intent_variant=(str(value["intent_variant"]) if value.get("intent_variant") else None),
            topology=str(value["topology"]),
            reference_state=str(value["reference_state"]),
            fault=dict(value["fault"]),
            bindings=dict(value["bindings"]),
            oracles=oracles,
            preservation_applicable=applicable,
            fault_applicable=fault_applicable,
            success_criteria=(dict(value["success_criteria"]) if isinstance(value.get("success_criteria"), Mapping) else None),
            seed=int(value.get("seed", 0)),
            method=(dict(value["method"]) if isinstance(value.get("method"), Mapping) else None),
        )


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    scenario_path: str
    testbed: Mapping[str, Any]
    execution_budget_seconds: float = 300.0
    result_dir: str = "reports/manual/results"
    #: Where the report derived from each result is written. Never `result_dir`:
    #: campaign scripts and summaries collect results as `*.json` in their own
    #: directory, and a `RUN.report.json` beside them would be taken for one.
    report_dir: str = "reports/manual"
    cleanup: str = "restore"
    # On a dual-homed edge the measured flow's path is a per-flow hash, so a single
    # draw that failed to see the fault is not evidence the fault is absent. Each
    # retry restores, re-injects and measures a fresh flow.
    injection_attempts: int = 1
    #: The model the campaign meant to run, as the judge was told. None when the
    #: judge was not told: provenance then says so instead of copying what the
    #: subject reported, which is the value it is meant to be checked against.
    configured_model: str | None = None
    #: The campaign this episode belongs to, when it belongs to one. Every
    #: record of one campaign carries the same value, so a whole campaign can
    #: be found -- and replayed -- from any single record it produced. None for
    #: an episode run on its own, which is identified by its scenario seed alone.
    seed_campaign: int | None = None
    #: The model that judges the subject's diagnosis against the injected fault
    #: (evaluation parameter 13): `model` and `api_base`, and `api_key` when the
    #: endpoint takes one. None when no judge is configured: the episode then
    #: records the diagnosis and says it was not judged, never a zero.
    diagnosis_judge: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExperimentConfig":
        cleanup = str(value.get("cleanup", "restore"))
        if cleanup not in {"restore", "destroy"}:
            raise ValueError("cleanup must be restore or destroy")
        execution_budget = float(value.get("execution_budget_seconds", 300.0))
        if execution_budget <= 0:
            raise ValueError("execution_budget_seconds must be greater than zero")
        judge = value.get("diagnosis_judge")
        if judge is not None:
            if not isinstance(judge, Mapping) or not judge.get("model") or not judge.get("api_base"):
                raise ValueError("diagnosis_judge needs a model and an api_base")
            judge = {key: judge[key] for key in ("model", "api_base", "api_key", "reading", "top_candidates", "audit_log", "extra_body")
                     if judge.get(key)}
        return cls(
            experiment_id=str(value["experiment_id"]),
            scenario_path=str(value["scenario_path"]),
            testbed=dict(value["testbed"]),
            execution_budget_seconds=execution_budget,
            result_dir=str(value.get("result_dir", "reports/manual/results")),
            report_dir=str(value.get("report_dir", "reports/manual")),
            cleanup=cleanup,
            injection_attempts=max(1, int(value.get("injection_attempts", 1))),
            configured_model=(str(value["configured_model"])
                              if value.get("configured_model") else None),
            seed_campaign=(int(value["seed_campaign"])
                             if value.get("seed_campaign") is not None else None),
            diagnosis_judge=judge,
        )


@dataclass
class OperationCounts:
    """Operation categories are deliberately disjoint."""

    llm_calls: int = 0
    ani_reads: int = 0
    ani_mutations: int = 0
    validations: int = 0
    successful_mutations: int = 0
    successful_reads: int = 0
    failed_operations: int = 0
    unsafe_operations: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def merge(self, other: "OperationCounts") -> None:
        for name in (
            "llm_calls",
            "ani_reads",
            "ani_mutations",
            "validations",
            "successful_mutations",
            "successful_reads",
            "failed_operations",
            "unsafe_operations",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            incoming = getattr(other, name)
            if incoming is not None:
                setattr(self, name, (getattr(self, name) or 0) + incoming)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProbeEvaluation:
    probe_id: str
    passed: bool
    metrics: Mapping[str, Any]
    thresholds: tuple[Mapping[str, Any], ...]
    threshold_results: tuple[bool, ...]


@dataclass(frozen=True)
class OracleEvaluation:
    oracle_id: str
    version: str
    phase: str
    passed: bool
    probes: tuple[ProbeEvaluation, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SUTResponse:
    result: Mapping[str, Any]
    identity: str
    version: str
    reported_model: str | None = None
    provider_model: str | None = None
    operations: OperationCounts = field(default_factory=OperationCounts)
    runtime_parameters: Mapping[str, Any] = field(default_factory=dict)
    serving: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class BenchmarkPlatform(Protocol):
    def deploy_or_reset(self, testbed: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def apply_reference_state(self, reference_state: str) -> Mapping[str, Any]: ...
    def inject_fault(self, fault: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def wait_for_convergence(self) -> Mapping[str, Any]: ...
    def restore_fault(self) -> Mapping[str, Any]: ...
    def restore_or_destroy(self, cleanup: str) -> Mapping[str, Any]: ...


@runtime_checkable
class ProbeRunner(Protocol):
    def run_probe(self, probe: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class SUTClient(Protocol):
    def invoke(self, task: Mapping[str, Any]) -> SUTResponse: ...


@runtime_checkable
class ResultSink(Protocol):
    def write(self, result: Mapping[str, Any], output_dir: str | Path) -> Path: ...
