from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from benchmarks.core import OracleEvaluator
from benchmarks.platforms.containerlab.compiled_topology import CompiledContainerLabTopology
from benchmarks.platforms.containerlab.platform import ContainerlabPlatform, ProbeExecutionError
from benchmarks.platforms.containerlab.types import CommandResult, Observation
from scenarios.oracle_loader import load_oracle, resolve_oracle


ROOT = Path(__file__).resolve().parents[4]
TOPOLOGY = ROOT / "scenarios/topologies/sme_leaf_spine_dmz_small.yaml"
PRESERVATION_ORACLE = ROOT / "scenarios/oracles/connectivity/preservation-v1.yaml"
# Every connectivity requirement of the small topology ends on web1 or app1, so
# this affected set leaves the unaffected scope with no path to measure.
ALL_PATHS_AFFECTED = ["web1", "app1"]


def _platform_with_ping_recorder() -> tuple[ContainerlabPlatform, list[dict]]:
    """A platform whose only way to ping is a stub that records each call."""
    platform = ContainerlabPlatform()
    platform.topology_document = CompiledContainerLabTopology.from_file(TOPOLOGY).document
    pings: list[dict] = []

    def record(probe):
        pings.append(dict(probe))
        return {
            "success": True,
            "packet_loss_percent": 0.0,
            "rtt_min_ms": 1.0,
            "rtt_avg_ms": 1.0,
            "rtt_max_ms": 1.0,
        }

    platform._icmp = record  # type: ignore[method-assign]
    return platform, pings


def test_connectivity_matrix_expands_all_bidirectional_requirements() -> None:
    platform = ContainerlabPlatform()
    platform.topology_document = CompiledContainerLabTopology.from_file(TOPOLOGY).document
    platform._icmp = lambda probe: {  # type: ignore[method-assign]
        "success": True,
        "packet_loss_percent": 0.0,
        "rtt_min_ms": 1.0,
        "rtt_avg_ms": 1.0,
        "rtt_max_ms": 1.0,
    }
    common = {"attempts": 1, "timeout_seconds": 1}

    all_paths = platform._connectivity_matrix({**common, "scope": "all"})
    affected = platform._connectivity_matrix({
        **common,
        "scope": "affected",
        "affected_nodes": ["user1"],
    })
    unaffected = platform._connectivity_matrix({
        **common,
        "scope": "unaffected",
        "affected_nodes": ["user1"],
    })

    assert all_paths["total_checks"] == 10
    assert affected["total_checks"] == 4
    assert unaffected["total_checks"] == 6
    assert all_paths["failed_checks"] == 0
    assert "vacuous" not in unaffected


def test_unaffected_scope_with_no_paths_is_a_vacuous_pass_without_pinging() -> None:
    platform, pings = _platform_with_ping_recorder()

    outcome = platform._connectivity_matrix({
        "attempts": 1,
        "timeout_seconds": 1,
        "scope": "unaffected",
        "affected_nodes": ALL_PATHS_AFFECTED,
    })

    assert pings == []
    assert outcome == {
        "success": True,
        "total_checks": 0,
        "passed_checks": 0,
        "failed_checks": 0,
        "checks": [],
        "vacuous": True,
        "reason": "scope unaffected selected no paths: every required path touches an affected node ['app1', 'web1']",
    }


def test_affected_scope_with_no_paths_still_raises() -> None:
    platform, pings = _platform_with_ping_recorder()

    with pytest.raises(ProbeExecutionError, match="scope 'affected' selected no paths"):
        platform._connectivity_matrix({
            "attempts": 1,
            "timeout_seconds": 1,
            "scope": "affected",
            # admin1 is a topology node that no connectivity requirement mentions.
            "affected_nodes": ["admin1"],
        })
    assert pings == []


def test_topology_without_connectivity_requirements_raises_for_every_scope() -> None:
    platform, pings = _platform_with_ping_recorder()
    platform.topology_document = {
        "topology": {**platform.topology_document["topology"], "connectivity_requirements": []}
    }

    for scope in ("all", "affected", "unaffected"):
        with pytest.raises(ProbeExecutionError, match="connectivity_requirements"):
            platform._connectivity_matrix({
                "attempts": 1,
                "timeout_seconds": 1,
                "scope": scope,
                "affected_nodes": ALL_PATHS_AFFECTED,
            })
    assert pings == []


def test_non_empty_unaffected_scope_still_pings_only_the_unaffected_paths() -> None:
    platform, pings = _platform_with_ping_recorder()

    outcome = platform._connectivity_matrix({
        "attempts": 1,
        "timeout_seconds": 1,
        "scope": "unaffected",
        "affected_nodes": ["user1"],
    })

    # guest1 -> web1, finance1 -> app1, external1 -> web1, each in both directions
    assert len(pings) == 6
    assert outcome["total_checks"] == 6
    assert outcome["passed_checks"] == 6
    assert outcome["failed_checks"] == 0
    assert outcome["success"] is True
    assert "vacuous" not in outcome
    assert "reason" not in outcome
    assert {check["source"] for check in outcome["checks"]} == {
        "guest1", "web1", "finance1", "app1", "external1"
    }
    assert not any("user1" in (check["source"], check["destination"]) for check in outcome["checks"])


def test_preservation_oracle_passes_vacuously_through_the_evaluator() -> None:
    platform, pings = _platform_with_ping_recorder()
    oracle = resolve_oracle(
        load_oracle(PRESERVATION_ORACLE, expected_version="1.0.0"),
        {"affected_nodes": ALL_PATHS_AFFECTED},
    )

    evaluation = OracleEvaluator(platform).evaluate(oracle)

    assert pings == []
    assert evaluation.passed is True
    assert evaluation.phase == "preservation"
    (probe,) = evaluation.probes
    assert probe.probe_id == "unaffected_paths"
    assert probe.passed is True
    assert probe.threshold_results == (True,)
    assert probe.metrics["vacuous"] is True
    assert probe.metrics["total_checks"] == 0
    assert probe.metrics["failed_checks"] == 0


FIREWALL_TOPOLOGY = ROOT / "scenarios/topologies/sme_leaf_firewall_dmz.yaml"
FILTERING_ORACLES = ROOT / "scenarios/oracles/filtering"


def _firewall_platform(open_paths: dict[tuple[str, str], float]) -> tuple[ContainerlabPlatform, list[dict]]:
    """The firewall lab, pinging with a stub that answers per (source, destination).

    Each listed pair answers with its packet loss; every other pair answers with
    full loss, which is what the healthy policy does to an isolated pair.
    """
    platform = ContainerlabPlatform()
    platform.topology_document = CompiledContainerLabTopology.from_file(FIREWALL_TOPOLOGY).document
    topology = platform.topology_document["topology"]
    names = {_address(topology, node): node for node in topology["nodes"] if _has_address(topology, node)}
    pings: list[dict] = []

    def ping(probe):
        pings.append(dict(probe))
        loss = open_paths.get((str(probe["source"]), names[str(probe["destination"])]), 100.0)
        return {
            "success": loss == 0.0,
            "packet_loss_percent": loss,
            "rtt_min_ms": None if loss == 100.0 else 1.0,
            "rtt_avg_ms": None if loss == 100.0 else 1.0,
            "rtt_max_ms": None if loss == 100.0 else 1.0,
        }

    platform._icmp = ping  # type: ignore[method-assign]
    return platform, pings


def _address(topology: dict, node: str) -> str:
    from benchmarks.platforms.containerlab.platform import _node_ip

    return _node_ip(topology, node)


def _has_address(topology: dict, node: str) -> bool:
    try:
        _address(topology, node)
    except ProbeExecutionError:
        return False
    return True


def test_isolation_matrix_passes_when_every_isolated_pair_is_blocked() -> None:
    platform, pings = _firewall_platform({})

    outcome = platform._isolation_matrix({"attempts": 3, "timeout_seconds": 1})

    assert len(pings) == outcome["total_checks"] == 6
    assert outcome["blocked_checks"] == 6
    assert outcome["leaked_checks"] == 0
    assert outcome["success"] is True
    assert ("web1", "finance1") in {(check["source"], check["destination"]) for check in outcome["checks"]}


def test_a_single_reply_is_a_leak() -> None:
    """A policy that lets one packet in three through is open, not mostly closed."""
    platform, _ = _firewall_platform({("web1", "finance1"): 66.7})

    outcome = platform._isolation_matrix({"attempts": 3, "timeout_seconds": 1})

    leaked = [(check["source"], check["destination"]) for check in outcome["checks"] if check["leaked"]]
    assert leaked == [("web1", "finance1")]
    assert outcome["leaked_checks"] == 1
    assert outcome["blocked_checks"] == 5
    assert outcome["success"] is False


def test_isolation_matrix_ignores_the_affected_scope() -> None:
    """A widened rule leaks between nodes the fault never touched."""
    platform, pings = _firewall_platform({})

    outcome = platform._isolation_matrix({
        "attempts": 1, "timeout_seconds": 1, "scope": "unaffected", "affected_nodes": ["web1", "app1"],
    })

    assert outcome["total_checks"] == len(pings) == 6


def test_topology_without_isolation_requirements_raises_rather_than_passing() -> None:
    platform, pings = _firewall_platform({})
    topology = dict(platform.topology_document["topology"])
    topology.pop("isolation_requirements")
    platform.topology_document = {"topology": topology}

    with pytest.raises(ProbeExecutionError, match="isolation_requirements"):
        platform._isolation_matrix({"attempts": 1, "timeout_seconds": 1})
    assert pings == []


def test_bidirectional_isolation_pairs_are_checked_both_ways() -> None:
    platform, _ = _firewall_platform({})
    topology = dict(platform.topology_document["topology"])
    topology["isolation_requirements"] = [{"source": "guest1", "destination": "finance1", "bidirectional": True}]
    platform.topology_document = {"topology": topology}

    outcome = platform._isolation_matrix({"attempts": 1, "timeout_seconds": 1})

    assert [(check["source"], check["destination"]) for check in outcome["checks"]] == [
        ("guest1", "finance1"), ("finance1", "guest1"),
    ]


@pytest.mark.parametrize("phase", ["healthy", "repair"])
def test_the_filtering_oracle_fails_an_accept_all_repair(phase: str) -> None:
    """Measured in e10 (filtering.zone_policy_enforcement.high.m2): turning the
    injected match-all deny into a match-all accept restored every required path
    and passed the old oracle. It also opens web1 -> finance1."""
    scenario = yaml.safe_load((ROOT / "scenarios/filtering/zone_policy_enforcement.yaml").read_text())
    pinned = scenario["oracles"][phase]["version"]
    oracle = resolve_oracle(
        load_oracle(FILTERING_ORACLES / f"{phase}-v1.yaml", expected_version=pinned),
        {"affected_nodes": ["app1", "web1"]},
    )
    topology = CompiledContainerLabTopology.from_file(FIREWALL_TOPOLOGY).document["topology"]
    required: dict[tuple[str, str], float] = {}
    for requirement in topology["connectivity_requirements"]:
        required[(requirement["source"], requirement["destination"])] = 0.0
        if requirement.get("bidirectional"):
            required[(requirement["destination"], requirement["source"])] = 0.0

    restored, _ = _firewall_platform(required)
    assert OracleEvaluator(restored).evaluate(oracle).passed is True

    widened, _ = _firewall_platform({**required, ("web1", "finance1"): 0.0})
    evaluation = OracleEvaluator(widened).evaluate(oracle)
    assert evaluation.passed is False
    required_paths, denied_paths = evaluation.probes
    assert required_paths.passed is True
    assert denied_paths.probe_id == "denied_paths"
    assert denied_paths.passed is False
    assert denied_paths.metrics["leaked_checks"] == 1


def test_reference_state_waits_for_configured_convergence(tmp_path: Path) -> None:
    state = tmp_path / "healthy.json"
    state.write_text(json.dumps({"id": "healthy", "commands": []}), encoding="utf-8")

    class FakeEnv:
        wait_arguments = None

        def apply_state(self, value):
            return [CommandResult(target="lab", command="apply", returncode=0)]

        def wait_until_healthy(self, timeout_seconds, interval_seconds):
            self.wait_arguments = (timeout_seconds, interval_seconds)
            return False, Observation(lab_name="sme01-small", nodes=[])

    env = FakeEnv()
    platform = ContainerlabPlatform()
    platform.env = env  # type: ignore[assignment]
    platform._healthy_timeout_seconds = 240
    platform._healthy_interval_seconds = 2

    outcome = platform.apply_reference_state(str(state))

    assert env.wait_arguments == (240, 2)
    assert outcome["ok"] is False
    assert outcome["converged"] is False
    assert "did not converge" in outcome["error"]


def test_repair_wait_uses_independent_convergence_window() -> None:
    class FakeEnv:
        wait_arguments = None

        def wait_until_healthy(self, timeout_seconds, interval_seconds):
            self.wait_arguments = (timeout_seconds, interval_seconds)
            return True, Observation(lab_name="sme01-small", nodes=[])

    env = FakeEnv()
    platform = ContainerlabPlatform()
    platform.env = env  # type: ignore[assignment]
    platform._repair_convergence_timeout_seconds = 30
    platform._repair_convergence_interval_seconds = 2

    outcome = platform.wait_for_convergence()

    assert env.wait_arguments == (30, 2)
    assert outcome == {
        "ok": True,
        "converged": True,
        "timeout_seconds": 30,
        "interval_seconds": 2,
        "observation": {
            "lab_name": "sme01-small",
            "nodes": [],
            "connectivity": [],
            "raw": {},
        },
        # No descriptor was loaded, so there is nobody to renew; said, not skipped.
        "lease_renewal": {"clients": []},
    }


DNS_TOPOLOGY = ROOT / "scenarios/topologies/sme_leaf_spine_dmz_dns.yaml"


def test_repair_wait_renews_every_dhcp_client_before_measuring() -> None:
    """The judge moves the clients' clock before it scores a DHCP repair.

    A lease here outlives the episode, so a server-side repair scored against the
    lease taken under the fault measures the clock. One renewal per DHCP client,
    before the convergence poll, and each one on the record.
    """
    class FakeEnv:
        calls: list[str] = []

        def wait_until_healthy(self, timeout_seconds, interval_seconds):
            self.calls.append("wait")
            return True, Observation(lab_name="sme01-dns", nodes=[])

    env = FakeEnv()
    platform = ContainerlabPlatform()
    platform.env = env  # type: ignore[assignment]
    platform.topology_document = CompiledContainerLabTopology.from_file(DNS_TOPOLOGY).document
    commands: list[tuple[str, str]] = []

    def run(source, command, *, timeout):
        commands.append((source, command))
        env.calls.append(f"renew:{source}")
        return CommandResult(target=source, command=command, returncode=0)

    platform._run = run  # type: ignore[method-assign]

    outcome = platform.wait_for_convergence()

    clients = [source for source, _ in commands]
    assert clients == ["admin1", "finance1", "guest1", "user1"], clients
    assert all("kill -USR2" in command and "ip -4 addr show eth1" in command for _, command in commands)
    assert env.calls[-1] == "wait" and env.calls[:-1] == [f"renew:{c}" for c in clients]
    assert outcome["lease_renewal"] == {"clients": [
        {"client": c, "ok": True, "returncode": 0} for c in clients]}


def test_a_client_whose_renewal_raises_does_not_stop_the_others() -> None:
    class FakeEnv:
        def wait_until_healthy(self, timeout_seconds, interval_seconds):
            return True, Observation(lab_name="sme01-dns", nodes=[])

    platform = ContainerlabPlatform()
    platform.env = FakeEnv()  # type: ignore[assignment]
    platform.topology_document = CompiledContainerLabTopology.from_file(DNS_TOPOLOGY).document

    def run(source, command, *, timeout):
        if source == "finance1":
            raise ProbeExecutionError("unknown probe source: finance1")
        return CommandResult(target=source, command=command, returncode=0)

    platform._run = run  # type: ignore[method-assign]
    renewal = platform.wait_for_convergence()["lease_renewal"]["clients"]
    assert [item["client"] for item in renewal] == ["admin1", "finance1", "guest1", "user1"]
    failed = next(item for item in renewal if item["client"] == "finance1")
    assert failed["ok"] is False and "unknown probe source" in failed["error"]
    assert all(item["ok"] for item in renewal if item["client"] != "finance1")


def test_failed_results_name_the_failing_command() -> None:
    """A non-raising failure must reach the lifecycle with the command and its output."""
    from benchmarks.platforms.containerlab.platform import _results
    from benchmarks.platforms.containerlab.types import CommandResult

    outcome = _results([
        CommandResult(target="user1", command="ip link set eth1 up", returncode=0),
        CommandResult(target="fw-ext", command="set firewall name X\nset firewall name Y",
                      returncode=1, stderr="Configuration path: [firewall name X] is not valid\nSet failed\n"),
    ])
    assert outcome["ok"] is False
    assert outcome["error"].startswith("fw-ext: return code 1 for 'set firewall name X'"), outcome["error"]
    assert "is not valid" in outcome["error"], outcome["error"]
    assert "error" not in _results([CommandResult(target="a", command="true", returncode=0)])
    assert _results([])["error"] == "no command ran"

    # The VyOS writer's normal refusal is non-raising: rc != 0 with a fixed reason and
    # the refusing line on stderr. Both must survive into the lifecycle's text.
    vyos = _results([CommandResult(
        target="fw-int", command="set firewall name A\nset firewall name B", returncode=1,
        reason="VyOS discarded the candidate configuration",
        stderr="ConfigSessionError: Configuration path: [firewall name B] is not valid\n\nSet failed\n")])
    assert "VyOS discarded the candidate configuration" in vyos["error"], vyos["error"]
    assert "[firewall name B] is not valid" in vyos["error"], vyos["error"]
    assert len(_results([CommandResult(target="x", command="y", returncode=1,
                                       reason="r" * 5000)])["error"]) < 400

    # Deployment semantics: only the last result decides, and it is the one named.
    deploy = _results([
        CommandResult(target="lab", command="containerlab destroy", returncode=1, stderr="no lab"),
        CommandResult(target="lab", command="containerlab deploy", returncode=0),
    ], require_last=True)
    assert deploy["ok"] is True and "error" not in deploy
    deploy = _results([
        CommandResult(target="lab", command="containerlab destroy", returncode=0),
        CommandResult(target="lab", command="containerlab deploy", returncode=1, stderr="boom"),
    ], require_last=True)
    assert deploy["error"].startswith("lab: return code 1 for 'containerlab deploy'"), deploy["error"]
