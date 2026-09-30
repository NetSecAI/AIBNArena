"""Validated, atomic benchmark result persistence."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


RESULT_SCHEMA_PATH = Path(__file__).with_name("result.schema.json")


def device_changes_of(report: Mapping[str, Any]) -> Any:
    """The subject's device changes, under the name the record uses.

    `device_changes` since ANI v0.2; `actions` in every record written before it, and
    the evaluation archive is full of those. Reading both keeps them readable. A reader
    of a record must come through here rather than name either key itself.
    """
    changes = report.get("device_changes")
    return report.get("actions") if changes is None else changes


def validate_result(result: Mapping[str, Any]) -> None:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(result),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid benchmark result: {details}")


class ResultWriter:
    def write(self, result: Mapping[str, Any], output_dir: str | Path) -> Path:
        validate_result(result)
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{result['run_id']}.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{result['run_id']}.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(target)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target
