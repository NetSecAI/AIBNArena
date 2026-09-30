"""The platform reads every device's running configuration the way the ANI does."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from benchmarks.platforms.containerlab.executor import ContainerLabExecutor
from benchmarks.platforms.containerlab.platform import ContainerlabPlatform
from benchmarks.platforms.containerlab.types import LabNode

LEAF = LabNode(name="leaf1", kind="nokia_srlinux", container_name="clab-test-leaf1")
FW = LabNode(name="fw1", kind="vyos", container_name="clab-test-fw1")
HOST = LabNode(name="host1", kind="linux", container_name="clab-test-host1")


class _ByContainerRunner:
    """Answers each device with its own configuration text."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def run(self, args, timeout_seconds=None, input_text=None):
        self.calls.append([str(item) for item in args])
        joined = " ".join(str(item) for item in args)
        if self.fail and self.fail in joined:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="device unreachable\n")
        if "clab-test-leaf1" in joined:
            out = "    interface ethernet-1/1 {\n        admin-state enable\n    }\n"
        elif "clab-test-fw1" in joined:
            out = "firewall {\n    zone dmz {\n    }\n}\n"
        elif "ip address show" in joined:
            out = "2: eth1: <UP>\n    inet 10.10.40.10/24 scope global eth1\n       valid_lft forever preferred_lft forever\n"
        elif "tc qdisc show" in joined:
            out = "qdisc htb 1: root refcnt 2 r2q 10 default 0x20\nclass htb 1:10 parent 1: prio 0 rate 8Mbit ceil 10Mbit\n"
        else:
            out = "default via 10.10.40.1 dev eth1\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def _platform(runner) -> ContainerlabPlatform:
    platform = ContainerlabPlatform()
    platform.env = SimpleNamespace(
        config=SimpleNamespace(nodes={"leaf1": LEAF, "fw1": FW, "host1": HOST}),
        executor=ContainerLabExecutor(Path("unused.yaml"), runner=runner),
    )
    return platform


def test_every_kind_is_read_with_its_own_command():
    runner = _ByContainerRunner()
    result = _platform(runner).collect_device_configurations()
    assert result["ok"] is True
    assert list(result["devices"]) == ["fw1", "host1", "leaf1"]          # name order
    leaf = result["devices"]["leaf1"]
    assert leaf["kind"] == "nokia_srlinux" and leaf["ok"] is True
    assert "info from running /" in leaf["command"] and "admin-state enable" in leaf["configuration"]
    fw = result["devices"]["fw1"]
    assert fw["kind"] == "vyos" and fw["configuration"].startswith("firewall {")
    host = result["devices"]["host1"]
    assert host["command"] == "ip address show; ip route show; tc qdisc/class/filter show"
    assert "inet 10.10.40.10/24" in host["configuration"] and "default via 10.10.40.1" in host["configuration"]
    # tc is configuration too: a shaping class shows in the snapshot, so a fault or a
    # repair made with tc shows in the before/after diff.
    assert "class htb 1:10" in host["configuration"]
    assert not any("set " in " ".join(call) or "commit" in " ".join(call) for call in runner.calls), "read-only"


def test_a_device_that_fails_keeps_its_error_and_the_others_stand():
    result = _platform(_ByContainerRunner(fail="clab-test-fw1")).collect_device_configurations()
    assert result["ok"] is False
    assert result["devices"]["fw1"]["ok"] is False and "unreachable" in result["devices"]["fw1"]["stderr"]
    assert result["devices"]["leaf1"]["ok"] is True and result["devices"]["host1"]["ok"] is True


def test_without_a_testbed_there_is_nothing_to_read():
    result = ContainerlabPlatform().collect_device_configurations()
    assert result["ok"] is False and result["devices"] == {}
