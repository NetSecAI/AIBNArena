"""The renewal shell the injector, the judge and the ANI share."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from benchmarks.platforms.containerlab import ani as ani_module
from benchmarks.platforms.containerlab.ani import ContainerLabANI
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology
from benchmarks.platforms.containerlab.dhcp_clients import (
    dhcp_client_nodes, dhcp_renew_shell, renew_dhcp_clients)
from benchmarks.platforms.containerlab.safety import check_command_safety
from benchmarks.platforms.containerlab.types import CommandResult

ROOT = Path(__file__).resolve().parents[4]
DNS = CompiledContainerLabTopology.from_file(ROOT / "scenarios/topologies/sme_leaf_spine_dmz_dns.yaml").document
SMALL = CompiledContainerLabTopology.from_file(ROOT / "scenarios/topologies/sme_leaf_spine_dmz_small.yaml").document


def test_clients_are_the_nodes_the_descriptor_assigns_by_dhcp() -> None:
    assert dhcp_client_nodes(DNS) == ["admin1", "finance1", "guest1", "user1"]
    assert dhcp_client_nodes(SMALL) == []
    assert dhcp_client_nodes(None) == []


def test_renewal_shell_releases_renews_waits_and_passes_the_safety_gate() -> None:
    shell = dhcp_renew_shell()
    for piece in ("kill -USR2", "kill -USR1", ": > /etc/resolv.conf",
                  "ip -4 addr show eth1", "while [ $i -lt 15 ]", "exit 0"):
        assert piece in shell, piece
    ok, reason = check_command_safety(shell)
    assert ok, reason
    assert "\n" not in shell


def test_injection_release_command_is_the_shared_shell() -> None:
    topology = CompiledContainerLabTopology.from_file(ROOT / "scenarios/topologies/sme_leaf_spine_dmz_dns.yaml")
    command = topology._dhcp_release_command({"client": "user1"})
    assert command.target == "user1" and command.mode == "shell"
    assert command.command == dhcp_renew_shell()


def test_renew_reports_every_client_and_survives_one_failure() -> None:
    seen = []

    def run(client, command):
        seen.append(client)
        if client == "guest1":
            raise RuntimeError("container gone")
        return CommandResult(target=client, command=command, returncode=0)

    outcome = renew_dhcp_clients(DNS, run)
    assert seen == ["admin1", "finance1", "guest1", "user1"]
    assert outcome["clients"][2] == {"client": "guest1", "ok": False, "error": "RuntimeError: container gone"}
    assert all(item["ok"] for item in outcome["clients"] if item["client"] != "guest1")
    assert renew_dhcp_clients(SMALL, run) == {"clients": []}


def _ani(document, monkeypatch):
    calls: list[tuple[str, str]] = []

    class Executor:
        def run_shell(self, node, command, timeout_seconds=None):
            calls.append((node.name, command))
            return CommandResult(target=node.name, command=command, returncode=0)

    nodes = {name: SimpleNamespace(name=name) for name in ("admin1", "finance1", "guest1", "user1")}
    env = SimpleNamespace(config=SimpleNamespace(nodes=nodes), executor=Executor())
    monkeypatch.setattr(ani_module, "VALIDATION_SETTLE_SECONDS", 0.0)
    return ContainerLabANI(env, topology_document=document), calls


def test_ani_settles_a_change_by_renewing_the_clients(monkeypatch) -> None:
    ani, calls = _ani(DNS, monkeypatch)
    assert ani._settle_after_change() is False and calls == []
    ani._change_applied_at = time.monotonic()
    assert ani._settle_after_change() is True
    assert [client for client, _ in calls] == ["admin1", "finance1", "guest1", "user1"]
    assert ani._last_lease_renewal["clients"][0] == {"client": "admin1", "ok": True, "returncode": 0}
    # One change earns one renewal.
    assert ani._settle_after_change() is False and len(calls) == 4


def test_ani_without_dhcp_clients_renews_nobody(monkeypatch) -> None:
    ani, calls = _ani(SMALL, monkeypatch)
    ani._change_applied_at = time.monotonic()
    assert ani._settle_after_change() is True
    assert calls == [] and ani._last_lease_renewal is None
