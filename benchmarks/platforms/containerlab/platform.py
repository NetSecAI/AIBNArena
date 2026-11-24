"""Containerlab implementation of the shared platform and generic probe contracts."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any, Mapping

from .ani import ContainerLabANI
from .compiled_topology import CompiledContainerLabTopology
from .dhcp_clients import renew_dhcp_clients
from .env import ContainerLabEnv
from .fault import LabFault
from . import segment_isolation
from .state import LabState
from .throughput import measure_throughput
from .types import CommandResult


_LOSS = re.compile(r"(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s+packet loss")
_RTT = re.compile(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = (?P<min>[0-9.]+)/(?P<avg>[0-9.]+)/(?P<max>[0-9.]+)/")

# Characters of the flood's last words carried into a voided sample's metrics.
# The tail is already a handful of lines; this keeps one long JSON line from
# swelling the run record.
_LOG_TAIL_METRIC_CHARS = 2000


class ProbeExecutionError(RuntimeError):
    pass


#: A matrix probe's `destination` when it measures the paths its oracle binds
#: (`paths: ${bindings.<name>}`) rather than the topology's own requirements: a
#: change request reshapes reachability, so the paths it is judged on are the
#: scenario's, not the descriptor's.
BOUND_PATHS = "bound_paths"


#: The traffic-control side of a Linux host's configuration, one line per interface
#: the way `tc ... show` prints it; `2>/dev/null || true` keeps a host without tc
#: (or an interface without a qdisc) from failing the read.
LINUX_TC_SNAPSHOT = (
    "sh -c 'for dev in $(ls /sys/class/net); do "
    "tc qdisc show dev $dev 2>/dev/null; tc class show dev $dev 2>/dev/null; "
    "tc filter show dev $dev 2>/dev/null; done; true'"
)


#: Devices `collect_structured_device_data` reads at once. Each read is a series of
#: read-only `docker exec`, independent of the others' (a cEOS switch answers thirteen
#: JSON commands, a Linux host seven plus three per interface).
STRUCTURED_READ_WORKERS = 8

#: Re-exported: the judge's lifecycle and its tests name them here.
from .priming import COMMAND_NOT_FOUND, data_plane_next_hops, prime_linux_nodes, prime_resolvers  # noqa: E402,F401


class ContainerlabPlatform:
    """Own testbed lifecycle, fault injection and reusable network probes."""

    def __init__(self) -> None:
        self.env: ContainerLabEnv | None = None
        self.topology_document: Mapping[str, Any] | None = None
        self._fault: LabFault | None = None
        self._reference_state: str | None = None
        self._healthy_timeout_seconds = 15.0
        self._healthy_interval_seconds = 2.0
        self._repair_convergence_timeout_seconds = 30.0
        self._repair_convergence_interval_seconds = 2.0

    def deploy_or_reset(self, testbed: Mapping[str, Any]) -> Mapping[str, Any]:
        if testbed.get("platform") != "containerlab":
            return {"ok": False, "error": "testbed platform must be containerlab"}
        topology = CompiledContainerLabTopology.from_file(str(testbed["topology_descriptor"]))
        self.topology_document = topology.document
        self._reference_state = str(testbed["reference_state"])
        self._healthy_timeout_seconds = float(testbed.get("healthy_timeout_seconds", 15.0))
        self._healthy_interval_seconds = float(testbed.get("healthy_interval_seconds", 2.0))
        self._repair_convergence_timeout_seconds = float(
            testbed.get("repair_convergence_timeout_seconds", 30.0)
        )
        self._repair_convergence_interval_seconds = float(
            testbed.get("repair_convergence_interval_seconds", 2.0)
        )
        self.env = ContainerLabEnv(topology.env_config(healthy_state_path=self._reference_state))
        results = self.env.reset()
        return _results(results, require_last=True)

    def apply_reference_state(self, reference_state: str) -> Mapping[str, Any]:
        env = self._env()
        self._reference_state = reference_state
        outcome = _results(env.apply_state(LabState.from_file(reference_state)))
        if not outcome["ok"]:
            return outcome
        converged, observation = env.wait_until_healthy(
            timeout_seconds=self._healthy_timeout_seconds,
            interval_seconds=self._healthy_interval_seconds,
        )
        return {
            **outcome,
            "ok": converged,
            "converged": converged,
            "observation": asdict(observation),
            **({} if converged else {"error": "reference state did not converge before timeout"}),
        }

    def inject_fault(self, fault: Mapping[str, Any]) -> Mapping[str, Any]:
        self._fault = LabFault.from_mapping(dict(fault))
        if self._fault.change_request and not self._fault.commands:
            # A change request breaks nothing: the network the subject is handed is
            # the reference one, and the degradation oracle checks that the change
            # it asks for is not already there. Only a fault that says so may inject
            # nothing; any other empty fault still fails below.
            self._env()
            return {"ok": True, "skipped": True, "results": [],
                    "reason": "change request: nothing is injected"}
        return _results(self._env().inject(self._fault))

    def wait_for_convergence(self) -> Mapping[str, Any]:
        """Wait for the repaired data plane before independent scoring.

        DHCP clients are renewed first. A server-side repair changes what the next
        lease says, and the lease the fault handed out lasts longer than the
        episode, so scoring without a renewal measures the client's clock rather
        than the repair. The injection already renews after every DHCP fault; this
        is the same clock being moved for the same reason, and the record says so.
        """
        renewal = self._renew_dhcp_clients()
        env = self._env()
        # A change request reshapes the required paths of the hosts it moves: they
        # are not meant to come back, and the repair oracle judges them on the
        # scenario's own paths. Convergence waits on every other required path.
        excluded = sorted(set(self._fault.expected_affected_nodes)) if (
            self._fault is not None and self._fault.change_request) else []
        if excluded:
            checks = [check for check in env.config.default_connectivity_checks
                      if check[0] not in excluded and check[1] not in excluded]
            converged, observation = env.wait_until_healthy(
                timeout_seconds=self._repair_convergence_timeout_seconds,
                interval_seconds=self._repair_convergence_interval_seconds,
                checks=checks,
            )
        else:
            converged, observation = env.wait_until_healthy(
                timeout_seconds=self._repair_convergence_timeout_seconds,
                interval_seconds=self._repair_convergence_interval_seconds,
            )
        return {
            "ok": converged,
            "converged": converged,
            "timeout_seconds": self._repair_convergence_timeout_seconds,
            "interval_seconds": self._repair_convergence_interval_seconds,
            "observation": asdict(observation),
            "lease_renewal": renewal,
            **({"excluded_nodes": excluded} if excluded else {}),
            **({} if converged else {"error": "repair did not converge before independent verification"}),
        }

    def _renew_dhcp_clients(self) -> Mapping[str, Any]:
        return renew_dhcp_clients(
            self.topology_document,
            lambda client, command: self._run(client, command, timeout=40))

    def restore_fault(self) -> Mapping[str, Any]:
        """Replay only the fault's own restore commands, leaving the testbed up.

        Narrower than `restore_or_destroy` on purpose: this is the undo half of a
        re-injection, so it must not also re-apply the reference state, which would
        cost a full convergence wait between two attempts at the same fault.
        """
        if self.env is None:
            return {"ok": False, "error": "testbed was not created"}
        if self._fault is None:
            return {"ok": False, "error": "no fault has been injected"}
        if not self._fault.restore_commands:
            return {"ok": True, "skipped": True, "reason": "fault declares no restore commands"}
        restore = LabFault(
            id=f"{self._fault.id}.restore",
            description="reference restoration between injection attempts",
            intent="restore",
            commands=self._fault.restore_commands,
        )
        with self.env.executor.idempotent_writes():
            return _results(self.env.inject(restore))

    def restore_or_destroy(self, cleanup: str) -> Mapping[str, Any]:
        if self.env is None:
            return {"ok": True, "skipped": True, "reason": "testbed was not created"}
        if cleanup == "destroy":
            return _results([self.env.stop()])
        results = []
        # Restoration puts the reference back; a device that already holds it
        # answers "nothing to commit", and that is the state we asked for.
        with self.env.executor.idempotent_writes():
            if self._fault and self._fault.restore_commands:
                restore = LabFault(
                    id=f"{self._fault.id}.restore",
                    description="reference restoration",
                    intent="restore",
                    commands=self._fault.restore_commands,
                )
                results.extend(self.env.inject(restore))
            if self._reference_state:
                results.extend(self.env.apply_state(LabState.from_file(self._reference_state)))
        return _results(results)

    def collect_device_configurations(self) -> Mapping[str, Any]:
        """The running configuration of every device, read the way the ANI's
        get_running_config reads it for the subject: SR Linux `info from running /`,
        VyOS the active configuration tree (cli-shell-api showConfig), Arista EOS
        `show running-config`, Linux
        `ip address show` and `ip route show`. Read-only. One entry per device, in
        name order; a device whose read failed keeps its error and the others stand.
        The judge snapshots this before and after the subject (`DeviceSnapshots`).
        """
        if self.env is None:
            return {"ok": False, "error": "testbed was not created", "devices": {}}
        devices: dict[str, dict[str, Any]] = {}
        ok = True
        for name, node in sorted(self.env.config.nodes.items()):
            try:
                if node.kind == "nokia_srlinux":
                    result = self.env.executor.run_srl_cli(node, "info from running /")
                elif node.kind == "vyos":
                    result = self.env.executor.run_vyos_show_config(node, [])
                elif node.kind == "arista_ceos":
                    result = self.env.executor.run_eos_cli(node, "show running-config")
                else:
                    address = self.env.executor.run_shell(node, "ip address show")
                    routes = self.env.executor.run_shell(node, "ip route show")
                    # Traffic control is configuration too: a shaping policy or a
                    # netem impairment lives in qdiscs, classes and filters, which
                    # `ip` never prints, so a fault or a repair made with `tc` left no
                    # trace in the before/after diff. Read per interface; a host
                    # without tc answers nothing rather than failing the snapshot.
                    traffic = self.env.executor.run_shell(node, LINUX_TC_SNAPSHOT)
                    result = CommandResult(
                        target=name,
                        command="ip address show; ip route show; tc qdisc/class/filter show",
                        returncode=max(address.returncode, routes.returncode),
                        stdout=f"{address.stdout}{routes.stdout}{traffic.stdout}",
                        stderr=f"{address.stderr}{routes.stderr}{traffic.stderr}",
                        safe=address.safe and routes.safe and traffic.safe,
                    )
            except Exception as exc:  # noqa: BLE001 - one device's failure must not hide the others
                ok = False
                devices[name] = {"kind": node.kind, "ok": False, "configuration": "",
                                 "error": f"{type(exc).__name__}: {exc}"}
                continue
            ok = ok and result.ok
            devices[name] = {
                "kind": node.kind, "command": result.command, "ok": result.ok,
                "returncode": result.returncode, "configuration": result.stdout, "stderr": result.stderr,
            }
        return {"ok": ok, "devices": devices}

    def collect_structured_device_data(self) -> Mapping[str, Any]:
        """Every device as `get_running_config(format="json")` answers a subject (SR Linux
        YANG state and running datastore, the VyOS configuration tree and its documents,
        the cEOS JSON views, the Linux commands), through an ANI over the same descriptor
        as the subject's. Each record also names the device's container (`host`) and the
        roles the descriptor gives it, so the record can be read alone, after the run
        (`DeviceSnapshots`).

        Every device. Read-only, devices in parallel; a device whose read failed keeps its
        error and the others stand.
        """
        if self.env is None:
            return {"ok": False, "error": "testbed was not created", "devices": {}}
        env = self.env
        ani = ContainerLabANI(env, topology_document=dict(self.topology_document or {}))

        def read(name: str) -> tuple[str, dict[str, Any]]:
            node = env.config.nodes[name]
            started = time.monotonic()
            try:
                answer = ani.get_running_config([name], output_format="json")
                record = dict(answer["nodes"][0])
            except Exception as exc:  # noqa: BLE001 - one device's failure must not hide the others
                record = {"name": name, "kind": node.kind, "ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}
            record.update(host=node.container_name, roles=ani.node_roles(name),
                          seconds=round(time.monotonic() - started, 3))
            return name, record

        names = sorted(env.config.nodes)
        with ThreadPoolExecutor(max_workers=max(1, min(STRUCTURED_READ_WORKERS, len(names)))) as pool:
            devices = dict(pool.map(read, names))
        return {"ok": all(record.get("ok") is True for record in devices.values()),
                "operation": "get_running_config", "format": "json", "devices": devices}

    def prime_learned_tables(self) -> Mapping[str, int]:
        """Make every Linux node send a frame to each of its next hops and every leased
        client ask its resolver, so that the switches and routers have learnt the MAC
        addresses a graph cables those nodes from. `gateway_pings` counts the next hops
        sent a frame, `resolver_queries` the queries answered. This is traffic, so the
        judge calls it only where no oracle measures: before the subject, once the
        degradation oracle has measured the faulty network (`prime_before_sut`), and at the
        end, once the oracles have measured the repaired one
        (`DeviceSnapshots.take_post_repair`).
        """
        return {"gateway_pings": prime_linux_nodes(self), "resolver_queries": prime_resolvers(self)}

    def run_probe(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        kind = str(probe["type"])
        if kind == "icmp":
            return self._icmp(probe)
        if kind == "tcp":
            return self._command_probe(probe, f"nc -z -w {float(probe['timeout_seconds']):g} {probe['destination']} {int(probe['port'])}")
        if kind == "http":
            scheme = str(probe.get("protocol", "http"))
            port = f":{int(probe['port'])}" if probe.get("port") else ""
            return self._command_probe(probe, f"curl -fsS --max-time {float(probe['timeout_seconds']):g} {scheme}://{probe['destination']}{port}/")
        if kind == "dns":
            return self._command_probe(probe, f"getent hosts {probe['destination']}")
        if kind == "throughput":
            return self._throughput(probe)
        if kind == "connectivity_matrix":
            return self._connectivity_matrix(probe)
        if kind == "isolation_matrix":
            return self._isolation_matrix(probe)
        if kind in {"security_event", "configuration"}:
            raise ProbeExecutionError(
                f"{kind} needs a scenario-specific executable probe; no implementation was declared"
            )
        raise ProbeExecutionError(f"unsupported probe type: {kind}")

    def _icmp(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        attempts = int(probe["attempts"])
        timeout = float(probe["timeout_seconds"])
        command = f"ping -q -c {attempts} -W {max(1, int(timeout))} {probe['destination']}"
        result = self._run(str(probe["source"]), command, timeout=max(30, attempts * (timeout + 1) + 5))
        output = f"{result.stdout}{result.stderr}"
        loss = _LOSS.search(output)
        rtt = _RTT.search(output)
        packet_loss = float(loss.group("loss")) if loss else 100.0
        return {
            "success": result.ok and packet_loss == 0.0,
            "packet_loss_percent": packet_loss,
            "rtt_min_ms": float(rtt.group("min")) if rtt else None,
            "rtt_avg_ms": float(rtt.group("avg")) if rtt else None,
            "rtt_max_ms": float(rtt.group("max")) if rtt else None,
        }

    def _command_probe(self, probe: Mapping[str, Any], command: str) -> Mapping[str, Any]:
        successes = 0
        for _ in range(int(probe["attempts"])):
            result = self._run(str(probe["source"]), command, timeout=float(probe["timeout_seconds"]) + 2)
            successes += int(result.ok)
        return {"success": successes == int(probe["attempts"]), "answer_count": successes, "http_status": 200 if successes else 0}

    def _throughput(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        """Measure the protected flow, through offered contention when asked.

        A bare `iperf3 -c` on an idle link cannot see a shaping policy at all: these
        faults never break reachability and only show up as a share lost while the
        link is disputed. The probe therefore declares what to measure and whether
        the oracle offers the competing flow itself, or the fault already is it.
        """
        measurement = probe.get("measurement")
        if measurement is None:
            raise ProbeExecutionError(
                f"throughput probe {probe['id']!r} declares no measurement block"
            )
        bindings = {
            key: value for key, value in measurement.items()
            if key not in {"seconds", "contended", "wan_edge", "shaped_interface", "fault_flood"}
        }
        circuit = None
        if measurement.get("wan_edge") is not None:
            circuit = (str(measurement["wan_edge"]), str(measurement["shaped_interface"]))
        fault_flood = bool(measurement.get("fault_flood"))
        if fault_flood and (bool(measurement["contended"]) or not measurement.get("background_source")):
            raise ProbeExecutionError(
                f"throughput probe {probe['id']!r} declares fault_flood: the flood is the fault's, "
                "so it needs background_source and contended: false"
            )
        result = measure_throughput(
            self._env(),
            bindings,
            seconds=int(measurement["seconds"]),
            # Declared by the oracle, because it is what separates the two shaped
            # families: assured_bandwidth injects the competing traffic as its own
            # fault, so the oracle must neither start a second flood nor stop that
            # one; the shaping family degrades a policy and leaves the link idle,
            # so the contention has to be offered.
            contended=bool(measurement["contended"]),
            # Named by the oracle when it caps the circuit, so a repair that
            # deleted or widened it fails instead of clearing the floor.
            circuit=circuit,
            # Declared by the oracle for the phases measured under the fault's own
            # flood: it is looked for, never touched, and a sample it did not cover
            # fails closed like one an offered flood did not.
            fault_flood=fault_flood,
        )
        # A sample the offered flood did not cover end to end is not a sample of
        # the policy: the idle circuit reads like an intact one. `success` alone
        # would not stop it: the judge thresholds throughput_mbps and nothing
        # else, so measure_throughput reports no throughput_mbps for such a
        # sample and every phase fails closed on it. The fields beside it say
        # which check the flood failed and what the flood itself reported.
        contention_observed = result.get("contention_observed")
        log_tail = result.get("contention_log_tail")
        return {
            "success": bool(result.get("ok")) and contention_observed is not False,
            "throughput_mbps": result.get("throughput_mbps"),
            "unverified_throughput_mbps": result.get("unverified_throughput_mbps"),
            # Protected and competing flow together, out of the shaped interface:
            # the oracle caps it near the link rate, so a repair that deleted or
            # widened the circuit instead of fixing the policy fails.
            "circuit_egress_mbps": result.get("circuit_egress_mbps"),
            "circuit_window_seconds": result.get("circuit_window_seconds"),
            "timed_out": bool(result.get("timed_out")),
            "contention_observed": contention_observed,
            "contention_attempts": result.get("contention_attempts"),
            "contention_checks": dict(result.get("contention_checks") or {}),
            "contention_log_tail": (
                None if log_tail is None else str(log_tail)[-_LOG_TAIL_METRIC_CHARS:]
            ),
            # Why a sample carries no throughput: the flood was not observed, or the
            # client never produced a report. Without it a failed phase says only
            # "throughput_mbps null".
            "error": result.get("error"),
        }

    def _connectivity_matrix(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.topology_document is None:
            raise ProbeExecutionError("connectivity matrix requires a deployed topology")
        topology = self.topology_document["topology"]
        if str(probe.get("destination")) == BOUND_PATHS:
            return self._bound_connectivity_matrix(probe, topology)
        requirements = topology.get("connectivity_requirements") or []
        if not requirements:
            raise ProbeExecutionError(
                "connectivity matrix requires a topology that declares connectivity_requirements; none found"
            )
        affected = {str(node) for node in probe.get("affected_nodes") or []}
        scope = str(probe.get("scope", "all"))
        paths: list[tuple[str, str]] = []
        for requirement in requirements:
            source = str(requirement["source"])
            destination = str(requirement["destination"])
            touches_affected = bool(affected.intersection({source, destination}))
            if scope == "affected" and not touches_affected:
                continue
            if scope == "unaffected" and touches_affected:
                continue
            paths.append((source, destination))
            if requirement.get("bidirectional"):
                paths.append((destination, source))
        if not paths:
            if scope == "unaffected":
                # An empty unaffected set is a vacuous pass, not a defect: the
                # preservation oracle asks whether anything unrelated to the fault
                # was broken, and when every required path touches an affected node
                # there is nothing unrelated to preserve. The record says nothing
                # was measured (zero checks, `vacuous` set) rather than claiming a
                # measured pass, so aggregate statistics over preservation results
                # must filter on the `vacuous` marker before counting this as
                # evidence of preserved connectivity.
                return {
                    "success": True,
                    "total_checks": 0,
                    "passed_checks": 0,
                    "failed_checks": 0,
                    "checks": [],
                    "vacuous": True,
                    "reason": (
                        f"scope {scope} selected no paths: every required path touches "
                        f"an affected node {sorted(affected)}"
                    ),
                }
            # An affected set that touches no required path is a binding defect:
            # the scenario points the oracle at nodes the topology never requires
            # connectivity for, so the measurement would be meaningless.
            raise ProbeExecutionError(f"connectivity matrix scope {scope!r} selected no paths")
        checks = []
        for source, destination in paths:
            metrics = self._icmp({
                "source": source,
                "destination": _node_ip(topology, destination),
                "attempts": probe["attempts"],
                "timeout_seconds": probe["timeout_seconds"],
            })
            checks.append({"source": source, "destination": destination, **metrics})
        passed = sum(bool(check["success"]) for check in checks)
        return {
            "success": passed == len(checks),
            "total_checks": len(checks),
            "passed_checks": passed,
            "failed_checks": len(checks) - passed,
            "checks": checks,
        }

    def _isolation_matrix(self, probe: Mapping[str, Any]) -> Mapping[str, Any]:
        """Ping every pair the policy keeps apart; any reply is a leak.

        The connectivity matrix sees a policy that became too strict and is blind
        to one that became too permissive: permitting everything repairs the
        broken pair and passes it. This is the other half. There is no scope:
        a widened rule leaks between nodes the fault never touched, so every
        isolated pair is checked whatever the affected set. A single reply is
        enough to leak -- a policy that lets one packet in three through is open.
        """
        if self.topology_document is None:
            raise ProbeExecutionError("isolation matrix requires a deployed topology")
        topology = self.topology_document["topology"]
        if str(probe.get("destination")) == BOUND_PATHS:
            return self._bound_isolation_matrix(probe, topology)
        requirements = topology.get("isolation_requirements") or []
        if not requirements:
            # Not a vacuous pass: nothing measured here would read as a policy
            # that holds, which is the one claim this probe exists to check.
            raise ProbeExecutionError(
                "isolation matrix requires a topology that declares isolation_requirements; none found"
            )
        paths: list[tuple[str, str]] = []
        for requirement in requirements:
            source = str(requirement["source"])
            destination = str(requirement["destination"])
            paths.append((source, destination))
            if requirement.get("bidirectional"):
                paths.append((destination, source))
        checks = []
        for source, destination in paths:
            metrics = self._icmp({
                "source": source,
                "destination": _node_ip(topology, destination),
                "attempts": probe["attempts"],
                "timeout_seconds": probe["timeout_seconds"],
            })
            leaked = float(metrics["packet_loss_percent"]) < 100.0
            checks.append({"source": source, "destination": destination, **metrics, "leaked": leaked})
        leaks = sum(bool(check["leaked"]) for check in checks)
        return {
            "success": leaks == 0,
            "total_checks": len(checks),
            "blocked_checks": len(checks) - leaks,
            "leaked_checks": leaks,
            "checks": checks,
        }

    def _bound_paths(self, probe: Mapping[str, Any]) -> list[tuple[str, str]]:
        try:
            paths = segment_isolation.parse_paths(probe.get("paths"))
        except ValueError as exc:
            raise ProbeExecutionError(f"probe {probe.get('id')!r}: {exc}") from exc
        if not paths:
            # An empty list would pass every threshold on zero checks.
            raise ProbeExecutionError(f"probe {probe.get('id')!r} measures bound_paths but binds none")
        return paths

    def _destination_addresses(
        self, probe: Mapping[str, Any], topology: Mapping[str, Any], node: str,
        cache: dict[str, list[str]],
    ) -> list[str]:
        """The addresses a bound path is pinged on: the one the descriptor declares, or
        with `addressing: live` every data-plane address the node holds now."""
        if str(probe.get("addressing", "declared")) != "live":
            return [_node_ip(topology, node)]
        if node not in cache:
            try:
                cache[node] = segment_isolation.live_addresses(
                    lambda source, command, timeout: self._run(source, command, timeout=timeout), node)
            except RuntimeError as exc:
                raise ProbeExecutionError(str(exc)) from exc
        return cache[node]

    def _bound_connectivity_matrix(self, probe: Mapping[str, Any], topology: Mapping[str, Any]) -> Mapping[str, Any]:
        """Ping the bound paths; each passes when its destination answers on one of its
        addresses. With `ttl`, the packets are sent with that time to live: at 1, only
        a destination in the source's own broadcast domain can answer."""
        run = lambda source, command, timeout: self._run(source, command, timeout=timeout)  # noqa: E731
        cache: dict[str, list[str]] = {}
        if str(probe.get("protocol")) == "tcp":
            # A connection to one port, the service's: it must open every time.
            if not probe.get("port"):
                raise ProbeExecutionError(f"probe {probe.get('id')!r} measures tcp bound_paths but names no port")
            checks = [
                segment_isolation.tcp_reach(
                    run, source, destination, self._destination_addresses(probe, topology, destination, cache),
                    int(probe["port"]), attempts=int(probe["attempts"]),
                    timeout_seconds=float(probe["timeout_seconds"]))
                for source, destination in self._bound_paths(probe)
            ]
        else:
            checks = [
                segment_isolation.reach(
                    run, source, destination, self._destination_addresses(probe, topology, destination, cache),
                    attempts=int(probe["attempts"]), timeout_seconds=float(probe["timeout_seconds"]),
                    ttl=(int(probe["ttl"]) if probe.get("ttl") else None))
                for source, destination in self._bound_paths(probe)
            ]
        passed = sum(bool(check["success"]) for check in checks)
        return {
            "success": passed == len(checks),
            "total_checks": len(checks),
            "passed_checks": passed,
            "failed_checks": len(checks) - passed,
            "checks": checks,
        }

    def _bound_isolation_matrix(self, probe: Mapping[str, Any], topology: Mapping[str, Any]) -> Mapping[str, Any]:
        """Ping the bound paths that must stay apart, on every address the destination
        holds; one reply on any of them is a leak."""
        run = lambda source, command, timeout: self._run(source, command, timeout=timeout)  # noqa: E731
        cache: dict[str, list[str]] = {}
        if str(probe.get("protocol")) == "tcp":
            checks = self._bound_tcp_isolation(probe, topology, run, cache)
        else:
            checks = [
                segment_isolation.isolation(
                    run, source, destination, self._destination_addresses(probe, topology, destination, cache),
                    attempts=int(probe["attempts"]), timeout_seconds=float(probe["timeout_seconds"]))
                for source, destination in self._bound_paths(probe)
            ]
        leaks = sum(bool(check["leaked"]) for check in checks)
        return {
            "success": leaks == 0,
            "total_checks": len(checks),
            "blocked_checks": len(checks) - leaks,
            "leaked_checks": leaks,
            "checks": checks,
        }

    def _bound_tcp_isolation(self, probe: Mapping[str, Any], topology: Mapping[str, Any], run: Any,
                             cache: dict[str, list[str]]) -> list[dict[str, Any]]:
        """Every TCP port the destination listens on (`ports: listening`), or the one the
        probe names, but those `open_services` keeps open: none may open a connection.

        Reading the ports on the host is what makes the check worth something: a port
        nothing listens on is refused whether a firewall is in the way or not.
        """
        try:
            opened = segment_isolation.parse_services(probe.get("open_services") or [])
        except ValueError as exc:
            raise ProbeExecutionError(f"probe {probe.get('id')!r}: {exc}") from exc
        checks = []
        for source, destination in self._bound_paths(probe):
            if str(probe.get("ports")) == "listening":
                try:
                    ports = segment_isolation.listening_tcp_ports(
                        lambda node, command, timeout: self._run(node, command, timeout=timeout), destination)
                except RuntimeError as exc:
                    raise ProbeExecutionError(str(exc)) from exc
            elif probe.get("port"):
                ports = [int(probe["port"])]
            else:
                raise ProbeExecutionError(
                    f"probe {probe.get('id')!r} measures tcp isolation but names neither a port nor ports: listening")
            ports = [port for port in ports if port not in opened.get(destination, set())]
            checks.append(segment_isolation.tcp_isolation(
                run, source, destination, self._destination_addresses(probe, topology, destination, cache),
                ports, attempts=int(probe["attempts"]), timeout_seconds=float(probe["timeout_seconds"])))
        return checks

    def _run(self, source: str, command: str, *, timeout: float):
        env = self._env()
        node = env.config.nodes.get(source)
        if node is None:
            raise ProbeExecutionError(f"unknown probe source: {source}")
        return env.executor.run_shell(node, command, timeout_seconds=timeout)

    def _env(self) -> ContainerLabEnv:
        if self.env is None:
            raise RuntimeError("Containerlab testbed has not been deployed")
        return self.env


def _results(results: list[Any], *, require_last: bool = False) -> dict[str, Any]:
    serialized = [{**asdict(item), "ok": item.ok} for item in results]
    ok = bool(results) and (results[-1].ok if require_last else all(item.ok for item in results))
    outcome: dict[str, Any] = {"ok": ok, "results": serialized}
    if not ok:
        # The lifecycle records only this text for a failed phase, so a command that
        # returned non-zero without raising must name itself here or reach the run
        # result as "unknown error".
        outcome["error"] = _first_failure(results, require_last=require_last)
    return outcome


def _first_failure(results: list[Any], *, require_last: bool) -> str:
    if not results:
        return "no command ran"
    candidates = [results[-1]] if require_last else [item for item in results if not item.ok]
    item = candidates[0] if candidates else results[-1]
    first_line = next((line for line in str(item.command or "").splitlines() if line.strip()), "")
    head = first_line[:160] if first_line else "<no command>"
    # The writer's fixed reason ("VyOS discarded the candidate configuration") says
    # what happened; the CLI's last lines say which command it refused. Both belong.
    parts = []
    if item.reason:
        parts.append(str(item.reason)[:200])
    lines = [line.strip() for line in (item.stderr or "").splitlines() if line.strip()] \
        or [line.strip() for line in (item.stdout or "").splitlines() if line.strip()]
    if lines:
        parts.append(" | ".join(lines[-3:])[-300:])
    detail = "; ".join(parts)
    return (f"{item.target}: return code {item.returncode} for {head!r}"
            + (f": {detail}" if detail else ""))


def _node_ip(topology: Mapping[str, Any], node: str) -> str:
    nodes = {**(topology.get("nodes") or {}), **(topology.get("optional_nodes") or {})}
    for interface in (nodes.get(node) or {}).get("interfaces", {}).values():
        address = (interface or {}).get("ipv4")
        if address:
            return str(address).split("/", 1)[0]
    raise ProbeExecutionError(f"topology has no IPv4 address for connectivity destination {node}")
