from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

APPLICABILITY_FILENAME = "topology_applicability.json"


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
    for domain in ("connectivity", "dhcp_dns", "filtering", "qos", "security"):
        domain_dir = root / domain
        if not domain_dir.exists():
            continue
        for path in sorted(domain_dir.glob("*.yaml")):
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
