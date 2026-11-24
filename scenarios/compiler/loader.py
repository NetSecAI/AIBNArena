from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

APPLICABILITY_FILENAME = "topology_applicability.json"

#: Catalog domains, in compilation order. `vlan` and `combined` come last so that a
#: suite compiled across every domain draws the earlier ones exactly as it did before
#: they existed; `combined` draws from its own streams anyway (see
#: ScenarioCompiler._compile_combination).
DOMAINS = ("connectivity", "dhcp_dns", "filtering", "qos", "security", "vlan", "combined")
COMBINED_DOMAIN = "combined"


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return payload


def discover_scenarios(root: str | Path) -> list[tuple[str, Path, dict[str, Any]]]:
    root = Path(root)
    items: list[tuple[str, Path, dict[str, Any]]] = []
    for domain in DOMAINS:
        domain_dir = root / domain
        if not domain_dir.exists():
            continue
        # The combined scenarios are filed by how many scenarios they join
        # (combined/2_scenarios/, ...); every other domain is flat.
        paths = domain_dir.rglob("*.yaml") if domain == COMBINED_DOMAIN else domain_dir.glob("*.yaml")
        for path in sorted(paths):
            payload = load_yaml(path)
            validate_scenario(domain, payload, path)
            items.append((domain, path, payload))
    return items


def load_topology_applicability(root: str | Path) -> dict[str, Any]:
    """Read the scenario/topology applicability map next to the scenarios.

    Returns an empty mapping when the file is absent, which compiles every
    scenario against every topology -- the behaviour that predates the map.
    """
    path = Path(root) / APPLICABILITY_FILENAME
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def scenario_applies_to_topology(
    applicability: dict[str, Any],
    relative_path: str,
    topology_id: str,
) -> bool:
    """Whether a scenario file may be compiled against a topology.

    A scenario with no entry applies everywhere, so the map only has to record
    the cases that are actually restricted.
    """
    entry = (applicability.get("scenarios") or {}).get(relative_path)
    if not entry:
        return True
    topologies = entry.get("topologies")
    if topologies is None:
        return True
    return topology_id in topologies


def validate_scenario(domain: str, payload: dict[str, Any], path: Path | None = None) -> None:
    label = str(path) if path else payload.get("scenario", "<scenario>")
    if "scenario" not in payload:
        raise ValueError(f"{label}: missing required field 'scenario'")
    if "version" not in payload:
        raise ValueError(f"{label}: missing required field 'version'")
    oracles = payload.get("oracles")
    if not isinstance(oracles, dict):
        raise ValueError(f"{label}: missing required field 'oracles'")
    required_oracles = {"healthy", "expected_degradation", "repair", "preservation"}
    missing_oracles = sorted(required_oracles.difference(oracles))
    if missing_oracles:
        raise ValueError(
            f"{label}: missing explicit oracle references: {', '.join(missing_oracles)}"
        )
    for phase in sorted(required_oracles):
        reference = oracles[phase]
        if not isinstance(reference, dict) or not reference.get("path") or not reference.get("version"):
            raise ValueError(f"{label}: {phase} oracle requires path and version")
    if "extra_info" not in payload and "methods" not in payload:
        raise ValueError(f"{label}: expected either 'methods' or legacy 'extra_info'")
    if domain == COMBINED_DOMAIN or "combination" in payload:
        validate_combination(payload, label)
        return
    if domain == "security":
        for key in ("task_mode", "security_properties", "stride"):
            if key not in payload:
                raise ValueError(f"{label}: missing required security field '{key}'")
        if not isinstance(payload["task_mode"], dict) or not payload["task_mode"]:
            raise ValueError(f"{label}: task_mode must be a non-empty mapping")
    else:
        if "task" not in payload:
            raise ValueError(f"{label}: missing required field 'task'")
        task_spec = payload["task"]
        if isinstance(task_spec, dict):
            # A named set of wordings varies how precisely the intent is stated. One of
            # them has to be the base, or every instance id would move the day a second
            # wording was added and no earlier result could be matched to a new one.
            if not task_spec:
                raise ValueError(f"{label}: task mapping must declare at least one wording")
            names = {str(name) for name in task_spec}
            base = payload.get("base_variant")
            if base is None:
                raise ValueError(
                    f"{label}: a task mapping requires base_variant, naming the wording "
                    f"that keeps the instance id the scenario had before the set existed")
            if str(base) not in names:
                raise ValueError(
                    f"{label}: base_variant {base!r} is not one of {sorted(names)}")
        elif "base_variant" in payload:
            raise ValueError(f"{label}: base_variant needs a task mapping to name")


#: The public success criteria a combinable scenario may declare. A combination is one
#: task with one public criterion list; reachability is the one criterion that means the
#: same thing whichever fault it is stated for.
COMBINABLE_CRITERIA = frozenset({"lab_connectivity"})


def combination_blocker(domain: str, payload: dict[str, Any]) -> str | None:
    """Why a scenario cannot be a component of a combination, or None when it can.

    A component must inject something -- a method the materializer renders -- and be
    judged on reachability alone. The security families bind by strategy and render no
    fault. The shaped QoS families are judged on a rate measured under contention between
    two named hosts across one circuit; a second fault anywhere on that path voids the
    measurement rather than adding a fault to repair, and their public criterion carries
    the measured flow, which a combined task could state only once.
    """
    if domain == COMBINED_DOMAIN or "combination" in payload:
        return "it is itself a combination"
    if not payload.get("methods"):
        return "it binds by strategy and declares no fault method to inject"
    criteria = (payload.get("success_criteria") or {}).get("all_of") or []
    types = {str(item.get("type")) for item in criteria if isinstance(item, dict)}
    if not types:
        return "it declares no public success criterion"
    extra = sorted(types - COMBINABLE_CRITERIA)
    if extra:
        return (f"it is judged on {', '.join(extra)} besides reachability, which a second fault "
                "on the measured path would void")
    return None


def validate_combination(payload: dict[str, Any], label: str) -> None:
    """The structure of a combined scenario, before any component is read.

    The components are the scenarios whose faults go in together; each method names one
    method id per component, in component order. A wording the file states (`task`) is
    used as written; the others are composed from the components' own, so `base_variant`
    names one of the wordings they all declare.
    """
    combination = payload.get("combination")
    if not isinstance(combination, dict):
        raise ValueError(f"{label}: a combined scenario requires a 'combination' mapping")
    components = combination.get("components")
    if not isinstance(components, list) or not 2 <= len(components) <= 4:
        raise ValueError(f"{label}: a combination has between 2 and 4 components")
    sources = []
    for component in components:
        if not isinstance(component, dict) or not all(
                component.get(key) for key in ("source", "scenario", "version")):
            raise ValueError(f"{label}: each component needs source, scenario and version")
        sources.append(str(component["source"]))
    if len(set(sources)) != len(sources):
        raise ValueError(f"{label}: a combination names each scenario once")
    if combination.get("blast_radius") != "disjoint":
        raise ValueError(f"{label}: combination.blast_radius must be 'disjoint'")
    # Tuples a check refused stay listed, so that no later run proposes them again and no
    # other tuple takes their id (an id names one tuple for the life of the file).
    withdrawn = combination.get("withdrawn") or []
    if not isinstance(withdrawn, list):
        raise ValueError(f"{label}: combination.withdrawn must be a list")
    withdrawn_tuples, withdrawn_ids = set(), set()
    for item in withdrawn:
        picks = item.get("components") if isinstance(item, dict) else None
        if (not isinstance(picks, list) or len(picks) != len(components)
                or not isinstance(item.get("id"), int) or not str(item.get("reason") or "").strip()):
            raise ValueError(f"{label}: each withdrawn tuple has an id, one method id per component "
                             "and a reason")
        withdrawn_tuples.add(tuple(picks))
        withdrawn_ids.add(item["id"])
    methods = payload.get("methods")
    if not isinstance(methods, list) or not methods:
        raise ValueError(f"{label}: a combined scenario lists at least one method")
    ids = set()
    for method in methods:
        picks = method.get("components") if isinstance(method, dict) else None
        if not isinstance(picks, list) or len(picks) != len(components):
            raise ValueError(
                f"{label}: method {method.get('id') if isinstance(method, dict) else method!r} "
                f"must name one method id per component ({len(components)})")
        if "selector" in method or "operation" in method:
            raise ValueError(f"{label}: a combined method takes its faults from its components")
        if tuple(picks) in withdrawn_tuples:
            raise ValueError(f"{label}: method {method.get('id')} is a withdrawn tuple")
        if method.get("id") in withdrawn_ids:
            raise ValueError(f"{label}: method id {method.get('id')} belongs to a withdrawn tuple")
        ids.add(method.get("id"))
    if len(ids) != len(methods):
        raise ValueError(f"{label}: method ids repeat")
    if "task_mode" in payload:
        raise ValueError(f"{label}: a combined scenario takes no task_mode")
    # A wording the file states is used as written; every other one is composed from the
    # components' own (ScenarioCompiler._combination_intent).
    task = payload.get("task")
    if task is not None and (not isinstance(task, dict) or not all(
            isinstance(text, str) and text.strip() for text in task.values())):
        raise ValueError(f"{label}: a combined scenario's task, when stated, maps wordings to text")
    if not payload.get("base_variant"):
        raise ValueError(f"{label}: a combined scenario names its base_variant")
