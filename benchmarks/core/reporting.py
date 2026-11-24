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


def repair_without_change_of(record: Mapping[str, Any]) -> bool | None:
    """`metrics.repair_without_change`, or the same audit derived from a record
    written before the marker: its repair verdict and its device snapshots. A reader
    must come through here, so E12-E16 are audited as the records after them are.

    The derivation answers None where the snapshots cannot vouch for "unchanged": a
    no-fault episode, no repair verdict, a snapshot missing or incomplete, or a Linux
    host read before traffic control was snapshotted (2026-09-26), since a netem or
    shaping repair would not show there.
    """
    metrics = record.get("metrics") if isinstance(record.get("metrics"), Mapping) else {}
    if "repair_without_change" in metrics:
        value = metrics["repair_without_change"]
        return value if isinstance(value, bool) else None
    if metrics.get("fault_applicable") is False:
        return None
    evaluations = record.get("evaluations") if isinstance(record.get("evaluations"), Mapping) else {}
    repair = evaluations.get("repair")
    if not isinstance(repair, Mapping) or not isinstance(repair.get("passed"), bool):
        return None
    manifest = record.get("device_configurations")
    if not isinstance(manifest, Mapping) or manifest.get("error"):
        return None
    snapshots = manifest.get("snapshots") if isinstance(manifest.get("snapshots"), Mapping) else {}
    for label in ("before_sut", "after_sut"):
        snapshot = snapshots.get(label)
        if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
            return None
        for device in (snapshot.get("devices") or {}).values():
            if device.get("kind") == "linux" and "tc " not in str(device.get("command") or ""):
                return None
    return repair["passed"] and not manifest.get("changed_by_sut")


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
