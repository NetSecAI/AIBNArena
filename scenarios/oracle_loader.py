"""Load and resolve declarative benchmark oracles without executing code."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator


ORACLE_SCHEMA_PATH = Path(__file__).with_name("schemas") / "oracle.schema.json"
_BINDING = re.compile(r"^\$\{bindings\.([A-Za-z][A-Za-z0-9_]*)\}$")


class OracleValidationError(ValueError):
    """Raised when an oracle is invalid or cannot be safely resolved."""


def _schema() -> dict[str, Any]:
    payload = json.loads(ORACLE_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):  # pragma: no cover - repository corruption
        raise OracleValidationError(f"oracle schema is not an object: {ORACLE_SCHEMA_PATH}")
    return payload


def validate_oracle(document: Mapping[str, Any], *, source: str = "<memory>") -> None:
    """Validate one oracle and report every schema error deterministically."""
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise OracleValidationError(f"invalid oracle {source}: {details}")


def load_oracle(path: str | Path, *, expected_version: str | None = None) -> dict[str, Any]:
    """Load safe YAML, validate it, and optionally pin the referenced version."""
    oracle_path = Path(path)
    document = yaml.safe_load(oracle_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise OracleValidationError(f"oracle must be a mapping: {oracle_path}")
    validate_oracle(document, source=str(oracle_path))
    actual_version = str(document["version"])
    if expected_version is not None and actual_version != expected_version:
        raise OracleValidationError(
            f"oracle version mismatch for {oracle_path}: expected {expected_version}, got {actual_version}"
        )
    return document


def resolve_oracle(document: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve exact binding placeholders; interpolation and expressions are forbidden.

    A composed oracle (one with a `compose` block, which the combined scenarios use)
    is assembled first from the oracles of the components the bindings name.
    """
    if "compose" in document:
        return _compose(document, bindings)
    resolved = _resolve(copy.deepcopy(dict(document)), bindings)
    validate_oracle(resolved, source=str(document.get("oracle_id", "<memory>")))
    return resolved


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _component_oracle_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else _REPOSITORY_ROOT / candidate


def _compose(document: Mapping[str, Any], bindings: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble a composed oracle from the oracles of the combination's components.

    Each component's oracle for this phase -- loaded at the version its scenario pins
    and resolved with the bindings that component drew -- becomes one group, so the
    component's own success rule (`any` across a DHCP fault's two symptoms, say) still
    decides it. A probe two components declare identically after resolution is kept
    once and counts for both: the required-paths matrix is measured a single time
    however many components ask for it. The oracle passes when every group does.
    """
    source = str(document.get("oracle_id", "<memory>"))
    components = bindings.get("components")
    if not isinstance(components, list) or len(components) < 2:
        raise OracleValidationError(
            f"composed oracle {source} needs bindings.components naming at least two components")
    phase = str(document["phase"])
    scope = str(document["compose"]["affected_nodes"])
    probes: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    groups: dict[str, Any] = {}
    for component in components:
        group = str(component.get("id") or "")
        reference = (component.get("oracles") or {}).get(phase)
        if not group or not isinstance(reference, Mapping):
            raise OracleValidationError(
                f"composed oracle {source}: a component has no id or no {phase} oracle reference")
        if group in groups:
            raise OracleValidationError(f"composed oracle {source}: component id {group} repeats")
        child = load_oracle(_component_oracle_path(str(reference["path"])),
                            expected_version=str(reference["version"]))
        if "compose" in child:
            raise OracleValidationError(f"composed oracle {source}: components cannot be composed oracles")
        if child["phase"] != phase:
            raise OracleValidationError(
                f"composed oracle {source}: {reference['path']} is a {child['phase']} oracle, not {phase}")
        child_bindings = dict(component.get("bindings") or {})
        if scope == "union":
            child_bindings["affected_nodes"] = list(bindings.get("affected_nodes") or [])
        resolved = resolve_oracle(child, child_bindings)
        groups[group] = copy.deepcopy(resolved["success"])
        for probe in resolved["probes"]:
            key = json.dumps({k: v for k, v in probe.items() if k not in {"id", "groups"}},
                             sort_keys=True, default=str)
            if key in seen:
                probes[seen[key]]["groups"].append(group)
                continue
            seen[key] = len(probes)
            probes.append({**probe, "id": f"{group}_{probe['id']}", "groups": [group]})
    composed = {
        "schema_version": document["schema_version"],
        "oracle_id": document["oracle_id"],
        "version": document["version"],
        "description": document.get("description", ""),
        "domain": document["domain"],
        "phase": phase,
        "probes": probes,
        "success": {"mode": "all_groups", "groups": groups},
    }
    if not composed["description"]:
        del composed["description"]
    validate_oracle(composed, source=source)
    return composed


def _resolve(value: Any, bindings: Mapping[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, bindings) for item in value]
    if not isinstance(value, str):
        return value
    match = _BINDING.fullmatch(value)
    if match:
        key = match.group(1)
        if key not in bindings:
            raise OracleValidationError(f"oracle references missing binding: {key}")
        replacement = bindings[key]
        if not isinstance(replacement, (str, int, float, bool, list)):
            raise OracleValidationError(f"oracle binding must be scalar or a list: {key}")
        if isinstance(replacement, list) and not all(
            isinstance(item, (str, int, float, bool)) for item in replacement
        ):
            raise OracleValidationError(f"oracle list binding must contain scalars: {key}")
        return copy.deepcopy(replacement)
    if "${" in value:
        raise OracleValidationError(
            "oracle placeholders must be exact ${bindings.name} values: " + repr(value)
        )
    return value
