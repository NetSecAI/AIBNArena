"""The judge keeps every device's configuration before and after the subject."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from benchmarks.core import BenchmarkLifecycle
from benchmarks.core.reporting import validate_result
from benchmarks.core.tests.test_lifecycle import (
    DEGRADED_DRAW, HEALTHY_DRAW, FakePlatform, _retry_fixture, _retry_lifecycle,
)
from scripts.generate_report import render_html, report_document

DRAWS = [HEALTHY_DRAW, DEGRADED_DRAW, HEALTHY_DRAW, HEALTHY_DRAW]


class SnapshotPlatform(FakePlatform):
    """leaf1 is broken by the fault and repaired by the subject; host1 only ages."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def collect_device_configurations(self) -> Mapping[str, Any]:
        self.reads += 1
        leaf = {1: "enable", 2: "disable", 3: "enable"}[self.reads]
        return {"ok": True, "devices": {
            "leaf1": {"kind": "nokia_srlinux", "ok": True, "command": "info from running /",
                      "configuration": f"    interface ethernet-1/1 {{\n        admin-state {leaf}\n    }}\n"},
            "host1": {"kind": "linux", "ok": True, "command": "ip address show; ip route show",
                      "configuration": f"    inet 10.0.0.2/24 scope global eth1\n       valid_lft {900 - self.reads}sec preferred_lft {900 - self.reads}sec\n"},
        }}


class RaisingPlatform(FakePlatform):
    def collect_device_configurations(self) -> Mapping[str, Any]:
        raise RuntimeError("management network down")


def _run(platform):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        scenario, documents, config = _retry_fixture(root, experiment_id="snapshots", injection_attempts=1)
        result_path = _retry_lifecycle(scenario, documents, config, platform, list(DRAWS)).run("experiment.toml")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        validate_result(payload)
        yield root, payload, result_path


def test_three_snapshots_with_their_diffs_sit_beside_the_record():
    platform = SnapshotPlatform()
    for root, payload, result_path in _run(platform):
        assert platform.reads == 3
        block = payload["device_configurations"]
        directory = Path(block["directory"])
        assert directory.parent == root / "results" / "devices"
        assert set(block["snapshots"]) == {"healthy", "before_sut", "after_sut"}
        for label in ("healthy", "before_sut", "after_sut"):
            assert block["snapshots"][label]["ok"] is True
            for device in ("leaf1", "host1"):
                entry = block["snapshots"][label]["devices"][device]
                assert entry["ok"] is True and (directory / entry["file"]).is_file()
        assert (directory / "leaf1" / "before_sut.txt").read_text().count("disable") == 1
        # The fault changed leaf1 and so did the subject; host1 only aged its lease.
        assert list(block["changes"]["healthy_to_before_sut"]) == ["leaf1"]
        assert list(block["changes"]["before_sut_to_after_sut"]) == ["leaf1"]
        assert block["changed_by_sut"] == ["leaf1"]
        change = block["changes"]["before_sut_to_after_sut"]["leaf1"]
        assert (change["lines_added"], change["lines_removed"]) == (1, 1)
        diff = (directory / change["file"]).read_text()
        assert "-        admin-state disable" in diff and "+        admin-state enable" in diff
        assert not (directory / "host1" / "before_sut_to_after_sut.diff").exists()
        assert json.loads((directory / "manifest.json").read_text())["changed_by_sut"] == ["leaf1"]
        for phase in ("snapshot_healthy", "snapshot_before_sut", "snapshot_after_sut"):
            assert payload["phase_durations"][phase] >= 0.0
        # The per-task report reads the block.
        document = report_document(payload, result_path, result_path.read_bytes())
        assert document["device_configurations"]["changed_by_sut"] == ["leaf1"]
        assert "Device configurations" in render_html(document)


def test_a_platform_without_the_capability_records_null():
    for _root, payload, _path in _run(FakePlatform()):
        assert payload["device_configurations"] is None
        assert payload["status"] == "completed"


def test_a_failing_read_is_recorded_and_the_episode_goes_on():
    for _root, payload, _path in _run(RaisingPlatform()):
        assert payload["status"] == "completed"
        block = payload["device_configurations"]
        assert block["snapshots"]["before_sut"]["ok"] is False
        assert "management network down" in block["snapshots"]["before_sut"]["error"]
        assert block["changes"] == {} and block["changed_by_sut"] == []
