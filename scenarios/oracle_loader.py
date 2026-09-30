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
    """Resolve exact binding placeholders; interpolation and expressions are forbidden."""
    resolved = _resolve(copy.deepcopy(dict(document)), bindings)
    validate_oracle(resolved, source=str(document.get("oracle_id", "<memory>")))
    return resolved


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
