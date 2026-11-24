"""The judge keeps every device's configuration before and after the subject."""
from __future__ import annotations

import dataclasses
import gzip
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from benchmarks.core import BenchmarkLifecycle, LifecycleError
from benchmarks.core.lifecycle import DeviceSnapshots
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


    def prime_learned_tables(self) -> Mapping[str, int]:
        self.actions.append("prime")
        return {"gateway_pings": 2, "resolver_queries": 1}

    def collect_structured_device_data(self) -> Mapping[str, Any]:
        # Right after the text read of the same moment; the last one has no text read.
        self.actions.append("structured")
        leaf = {2: "disable", 3: "enable"}[self.reads]
        return {"ok": False, "devices": {
            "leaf1": {"name": "leaf1", "kind": "nokia_srlinux", "ok": True, "host": "clab-test-leaf1",
                      "roles": ["leaf"], "state": {"interface": []},
                      "running": {"interface": [{"name": "ethernet-1/1", "admin-state": leaf}]}},
            "host1": {"name": "host1", "kind": "linux", "ok": False, "host": "clab-test-host1",
                      "roles": ["client"], "error": "no interface reported"},
        }}


class UntouchedPlatform(SnapshotPlatform):
    """The fault disables leaf1 and the subject leaves it so, while the draws still
    pass the repair oracle: the luck the five-ping QoS probe had on E12-E16."""

    def collect_device_configurations(self) -> Mapping[str, Any]:
        collected = super().collect_device_configurations()
        if self.reads == 3:
            leaf = collected["devices"]["leaf1"]
            leaf["configuration"] = leaf["configuration"].replace("enable", "disable")
        return collected


class RaisingPlatform(FakePlatform):
    def collect_device_configurations(self) -> Mapping[str, Any]:
        raise RuntimeError("management network down")


class StructuredReadFails(SnapshotPlatform):
    def collect_structured_device_data(self) -> Mapping[str, Any]:
        raise RuntimeError("docker daemon gone")


class PrimingFails(SnapshotPlatform):
    def prime_learned_tables(self) -> Mapping[str, int]:
        raise RuntimeError("no route to the gateway")


def _run(platform, draws=None):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        scenario, documents, config = _retry_fixture(root, experiment_id="snapshots", injection_attempts=1)
        result_path = _retry_lifecycle(scenario, documents, config, platform,
                                       list(DRAWS) if draws is None else draws).run("experiment.toml")
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
        # The subject changed leaf1, so its repair pass is its own.
        assert payload["metrics"]["repair_without_change"] is False
        for phase in ("snapshot_healthy", "snapshot_before_sut", "snapshot_after_sut"):
            assert payload["phase_durations"][phase] >= 0.0
        # The per-task report reads the block.
        document = report_document(payload, result_path, result_path.read_bytes())
        assert document["device_configurations"]["changed_by_sut"] == ["leaf1"]
        assert "Device configurations" in render_html(document)


def _structured(directory: Path, device: str, label: str) -> dict:
    return json.loads(gzip.decompress((directory / device / f"{label}.json.gz").read_bytes()))


def test_the_structured_read_is_kept_before_and_after_the_subject():
    for _root, payload, result_path in _run(SnapshotPlatform()):
        block = payload["device_configurations"]
        directory = Path(block["directory"])
        assert set(block["structured"]) == {"before_sut", "after_sut", "post_repair"}, "not healthy"
        assert not (directory / "leaf1" / "healthy.json.gz").exists()
        before, after = (_structured(directory, "leaf1", label) for label in ("before_sut", "after_sut"))
        # The record as the platform returned it, container and roles included: what a
        # graph is built from after the run.
        assert before["running"]["interface"][0]["admin-state"] == "disable"
        assert after["running"]["interface"][0]["admin-state"] == "enable"
        assert (before["host"], before["roles"]) == ("clab-test-leaf1", ["leaf"])
        summary = block["structured"]["after_sut"]
        assert summary["ok"] is False and summary["seconds"] >= 0.0
        assert summary["devices"]["leaf1"] == {
            "kind": "nokia_srlinux", "ok": True, "roles": ["leaf"],
            "file": "leaf1/after_sut.json.gz",
            "bytes": len(json.dumps(after, ensure_ascii=False, sort_keys=True).encode("utf-8"))}
        # A device whose read failed keeps its file and its error.
        assert summary["devices"]["host1"]["error"] == "no interface reported"
        assert _structured(directory, "host1", "after_sut")["ok"] is False
        assert json.loads((directory / "manifest.json").read_text())["structured"] == block["structured"]


def test_the_last_read_is_primed_and_comes_after_every_measurement():
    platform = SnapshotPlatform()

    def draws():
        for draw in DRAWS:
            platform.actions.append("probe")
            yield draw

    for _root, payload, _path in _run(platform, draws()):
        block = payload["device_configurations"]
        last = _structured(Path(block["directory"]), "leaf1", "post_repair")
        assert last["running"]["interface"][0]["admin-state"] == "enable"
        assert block["structured"]["post_repair"]["priming"] == {"gateway_pings": 2, "resolver_queries": 1}
        actions = platform.actions
        # The last priming: the first one is the subject's (prime_before_sut).
        prime = len(actions) - 1 - actions[::-1].index("prime")
        # Priming is traffic: nothing the verdict rests on may be measured after it.
        assert actions.index("wait_for_convergence") < prime
        assert max(index for index, action in enumerate(actions) if action == "probe") < prime
        assert actions[prime + 1] == "structured" and actions[prime + 2].startswith("cleanup:")
        assert payload["phase_durations"]["snapshot_post_repair"] >= 0.0


def test_the_subject_is_handed_a_primed_network():
    platform = SnapshotPlatform()

    def draws():
        for draw in DRAWS:
            platform.actions.append("probe")
            yield draw

    for _root, payload, _path in _run(platform, draws()):
        assert payload["priming_before_sut"] == {"gateway_pings": 2, "resolver_queries": 1}
        actions = platform.actions
        first_prime = actions.index("prime")
        probes = [index for index, action in enumerate(actions) if action == "probe"]
        # After the degradation draw, the last measurement before the subject (the
        # healthy draw and it are the first two), and before the read of the network the
        # subject is handed, which comes before any later measurement.
        assert probes[1] < first_prime < actions.index("structured") < probes[2]
        assert actions.count("prime") == 2
        assert payload["phase_durations"]["prime_before_sut"] >= 0.0


def test_a_failing_priming_is_recorded_and_the_read_goes_on():
    for _root, payload, _path in _run(PrimingFails()):
        read = payload["device_configurations"]["structured"]["post_repair"]
        assert read["priming"] == {"error": "RuntimeError: no route to the gateway"}
        assert read["devices"]["leaf1"]["ok"] is True
        # The subject is handed its task all the same.
        assert payload["priming_before_sut"] == {"error": "RuntimeError: no route to the gateway"}
        assert payload["sut_result"]["status"] == "completed"
        assert payload["status"] == "completed"


def test_a_platform_that_cannot_prime_records_null():
    for _root, payload, _path in _run(FakePlatform()):
        assert payload["priming_before_sut"] is None
        assert payload["status"] == "completed"


def test_a_replay_that_keeps_no_structured_read_neither_primes_nor_reads():
    platform = SnapshotPlatform()
    with tempfile.TemporaryDirectory() as temporary:
        snapshots = DeviceSnapshots(platform, Path(temporary), structured=False)
        snapshots.take_post_repair()
    assert platform.actions == [] and snapshots.structured == {}


def test_a_failing_structured_read_leaves_the_text_snapshot_standing():
    for _root, payload, _path in _run(StructuredReadFails()):
        block = payload["device_configurations"]
        assert block["structured"]["before_sut"] == {
            "ok": False, "error": "RuntimeError: docker daemon gone", "devices": {}}
        assert block["snapshots"]["before_sut"]["ok"] is True
        assert block["changed_by_sut"] == ["leaf1"]


def test_a_repair_passed_on_an_untouched_lab_is_marked_and_kept():
    for _root, payload, result_path in _run(UntouchedPlatform()):
        assert payload["device_configurations"]["changed_by_sut"] == []
        assert payload["evaluations"]["repair"]["passed"] is True
        assert payload["metrics"]["repair_without_change"] is True
        # An audit beside the verdict: the oracle's pass is not rewritten.
        assert payload["metrics"]["environment_success"] is True


def test_a_platform_without_the_capability_records_null():
    for _root, payload, _path in _run(FakePlatform()):
        assert payload["device_configurations"] is None
        assert payload["status"] == "completed"
        # No snapshot, so nothing can say the lab went untouched.
        assert payload["metrics"]["repair_without_change"] is None


def test_a_failing_read_is_recorded_and_the_episode_goes_on():
    for _root, payload, _path in _run(RaisingPlatform()):
        assert payload["status"] == "completed"
        block = payload["device_configurations"]
        assert block["snapshots"]["before_sut"]["ok"] is False
        assert "management network down" in block["snapshots"]["before_sut"]["error"]
        assert block["changes"] == {} and block["changed_by_sut"] == []
        # An empty change list read from failed snapshots is not "unchanged".
        assert payload["metrics"]["repair_without_change"] is None


class AnswerLostSUT:
    """The subject changed leaf1, then its answer never reached the judge."""

    def invoke(self, task: Mapping[str, Any]) -> Any:
        raise TimeoutError("A2A client timed out")


def test_a_failed_answer_still_records_what_the_subject_left():
    platform = SnapshotPlatform()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        scenario, documents, config = _retry_fixture(root, experiment_id="snapshots", injection_attempts=1)
        lifecycle = _retry_lifecycle(scenario, documents, config, platform, list(DRAWS))
        lifecycle = BenchmarkLifecycle(dataclasses.replace(lifecycle.dependencies, sut_client=AnswerLostSUT()))
        try:
            lifecycle.run("experiment.toml")
        except LifecycleError as error:
            payload = json.loads(error.result_path.read_text(encoding="utf-8"))
        else:
            raise AssertionError("a lost answer fails the episode")
        validate_result(payload)
        assert payload["error"]["phase"] == "invoke_sut", "the snapshot after it does not take the blame"
        assert platform.reads == 3
        block = payload["device_configurations"]
        assert block["snapshots"]["after_sut"]["ok"] is True
        assert block["changed_by_sut"] == ["leaf1"]
        assert payload["phase_durations"]["snapshot_after_sut"] >= 0.0
