"""Load benchmark experiment and testbed documents."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import tomllib

from benchmarks.core.contracts import ExperimentConfig, ScenarioDefinition
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology
from scenarios.access import CompiledScenarioSuite
from scenarios.compiler.compiler import ScenarioCompiler


REPOSITORY = Path(__file__).resolve().parents[2]


def _read_toml(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("rb") as stream:
        document = tomllib.load(stream)
    if not isinstance(document, dict):
        raise ValueError(f"{source}: expected a TOML mapping")
    return document


def _root_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY / path


def load_experiment(
    path: str | Path,
    *,
    experiment_id: str | None = None,
    execution_budget_seconds: float | None = None,
    result_dir: str | Path | None = None,
    report_dir: str | Path | None = None,
    configured_model: str | None = None,
    seed_campaign: int | None = None,
    cleanup: str | None = None,
    diagnosis_judge: Mapping[str, Any] | None = None,
) -> ExperimentConfig:
    """Compose an experiment with its testbed and benchmark-owned limits.

    `diagnosis_judge` overrides the suite's `[diagnosis_judge]` table (`model`,
    `api_base`, optional `api_key`) the way the other overrides do: given, it wins;
    None, the suite decides; a suite that names none runs unjudged.
    """
    suite_path = _root_path(path)
    suite = _read_toml(suite_path)
    testbed_path = _root_path(str(suite["testbed_config"]))
    testbed = _read_toml(testbed_path)
    for required in ("id", "platform", "topology_descriptor", "reference_state"):
        if required not in testbed:
            raise ValueError(f"{testbed_path}: missing {required}")
    return ExperimentConfig.from_mapping({
        "diagnosis_judge": (dict(diagnosis_judge) if diagnosis_judge is not None
                            else suite.get("diagnosis_judge")),
        "experiment_id": experiment_id or suite["experiment_id"],
        # The scenario loader reopens the suite to compile the selected instance.
        "scenario_path": str(suite_path),
        "testbed": {**testbed, "config_path": str(testbed_path)},
        "execution_budget_seconds": (
            execution_budget_seconds
            if execution_budget_seconds is not None
            else suite.get("execution_budget_seconds", 300)
        ),
        "result_dir": (
            str(result_dir)
            if result_dir is not None
            else suite.get("result_dir", "reports/manual/results")
        ),
        "report_dir": (
            str(report_dir)
            if report_dir is not None
            else suite.get("report_dir", "reports/manual")
        ),
        "cleanup": cleanup if cleanup is not None else suite.get("cleanup", "restore"),
        "injection_attempts": suite.get("injection_attempts", 1),
        "configured_model": (configured_model if configured_model is not None
                             else suite.get("configured_model")),
        "seed_campaign": (seed_campaign if seed_campaign is not None
                            else suite.get("seed_campaign")),
    })


def load_scenario(
    path: str | Path,
    *,
    scenario_id: str | None = None,
    seed: int | None = None,
    fault_applicable: bool | None = None,
) -> ScenarioDefinition:
    """Compile and select the scenario named by an experiment suite.

    `fault_applicable` False (or the suite key of that name) keeps the compiled
    instance, its bindings and oracles, and tells the lifecycle not to inject:
    the false-positive episode is the same plate on a healthy lab.
    """
    suite_path = _root_path(path)
    suite = _read_toml(suite_path)
    topology_path = _root_path(str(suite["topology_descriptor"]))
    scenarios_root = _root_path(str(suite.get("scenarios_root", "scenarios")))
    selected_id = str(scenario_id or suite["scenario_id"])
    selected_seed = int(seed if seed is not None else suite.get("seed", 0))
    domain = selected_id.split(".", 1)[0]
    compiler = ScenarioCompiler.from_topology_file(topology_path, seed=selected_seed)
    compiled = CompiledScenarioSuite(compiler.compile_scenarios(
        scenarios_root,
        domains={domain},
        expand_methods=True,
        samples_per_scenario=int(suite.get("samples_per_definition", 1)),
    ))
    selected = compiled.select([selected_id])[0]
    materializer = CompiledContainerLabTopology.from_file(topology_path)
    fault = materializer.materialize(selected)
    testbed = _read_toml(_root_path(str(suite["testbed_config"])))
    bindings = _oracle_bindings(selected.private["bindings"], materializer.document, domain)
    return ScenarioDefinition.from_mapping({
        "id": selected.id,
        "version": selected.document["version"],
        "domain": selected.domain,
        "intent": selected.intent,
        # Named only when the scenario declares several wordings of the task; the
        # id carries it too, but a reader should not have to parse an id.
        "intent_variant": selected.private.get("task_variant"),
        "topology": str(topology_path),
        "reference_state": str(_root_path(str(testbed["reference_state"]))),
        "fault": asdict(fault),
        "bindings": bindings,
        "oracles": selected.document["oracles"],
        "preservation_applicable": bool(suite.get("preservation_applicable", True)),
        "fault_applicable": (bool(fault_applicable) if fault_applicable is not None
                             else bool(suite.get("fault_applicable", True))),
        "success_criteria": selected.public_view().get("success_criteria"),
        "seed": selected_seed,
        # What was injected, in the compiler's terms, for the diagnosis judge.
        "method": (selected.private.get("evaluator") or {}).get("selected_method"),
    })


def _oracle_bindings(
    original: Mapping[str, Any], topology_document: Mapping[str, Any], domain: str
) -> dict[str, Any]:
    bindings = dict(original)
    topology = topology_document["topology"]
    requirements = list(topology.get("connectivity_requirements") or [])
    affected = {str(item) for item in bindings.get("affected_nodes") or []}

    if domain == "qos":
        bindings.setdefault("source", bindings.get("target"))
    primary = None
    if bindings.get("source") and bindings.get("destination"):
        primary = {"source": bindings["source"], "destination": bindings["destination"]}
    if primary is None:
        primary = next(
            (item for item in requirements if affected.intersection({str(item["source"]), str(item["destination"])})),
            requirements[0] if requirements else None,
        )
    if primary:
        bindings.setdefault("source", str(primary["source"]))
        bindings.setdefault("destination", str(primary["destination"]))
        bindings.setdefault("destination_ip", _node_ip(topology, str(primary["destination"])))

    if not affected and primary:
        affected = {str(primary["source"]), str(primary["destination"])}
    bindings["affected_nodes"] = sorted(affected)

    primary_nodes = {str(bindings.get("source")), str(bindings.get("destination"))}
    preserved = next(
        (item for item in requirements
         if not affected.intersection({str(item["source"]), str(item["destination"])})
         and {str(item["source"]), str(item["destination"])} != primary_nodes),
        None,
    )
    if preserved:
        bindings.setdefault("preservation_source", str(preserved["source"]))
        bindings.setdefault("preservation_destination", str(preserved["destination"]))
        bindings.setdefault("preservation_destination_ip", _node_ip(topology, str(preserved["destination"])))
    return bindings


def _node_ip(topology: Mapping[str, Any], node: str) -> str:
    spec = {**(topology.get("nodes") or {}), **(topology.get("optional_nodes") or {})}.get(node) or {}
    for interface in (spec.get("interfaces") or {}).values():
        address = (interface or {}).get("ipv4")
        if address:
            return str(address).split("/", 1)[0]
    raise ValueError(f"topology has no IPv4 address for oracle destination {node}")
