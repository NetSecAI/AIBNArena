from __future__ import annotations

import re

from .executor import ContainerLabExecutor
from .types import ConnectivityCheck, LabNode, Observation


_PACKET_LOSS_RE = re.compile(r"(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s+packet loss")
# `ping` echoes the address it resolved the name to on its first line, in both
# the busybox and the iputils spellings: `PING web1.sme01.example (10.10.40.10)`.
# Reading it back is what turns a name ping into a resolution check -- without it
# a record answering the wrong address would still pass, as long as whatever it
# points at replies.
_RESOLVED_IP_RE = re.compile(r"PING\s+\S+\s+\((?P<ip>[0-9]+(?:\.[0-9]+){3})\)")


class ContainerLabObserver:
    """Collects observations from Linux clients, SR Linux, and VyOS nodes."""

    def __init__(self, executor: ContainerLabExecutor, lab_name: str, nodes: dict[str, LabNode]):
        self.executor = executor
        self.lab_name = lab_name
        self.nodes = nodes

    def observe(
        self,
        connectivity_checks: list[tuple[str, ...]] | None = None,
    ) -> Observation:
        # A check is (source, destination, destination_ip) and optionally the
        # hostname to ping instead of the address, so a topology that declares no
        # DNS keeps emitting the three-element form it always did.
        checks = [self.ping(*check) for check in connectivity_checks or []]
        raw = self._basic_raw_observations()
        return Observation(
            lab_name=self.lab_name,
            nodes=list(self.nodes.values()),
            connectivity=checks,
            raw=raw,
        )

    def ping(
        self,
        source: str,
        destination: str,
        destination_ip: str,
        hostname: str | None = None,
    ) -> ConnectivityCheck:
        """Ping one node from another, by address or -- with ``hostname`` -- by name.

        A name ping is the only observation that crosses the resolver, so it is
        what makes a DNS or DHCP provisioning fault visible: the address checks
        of the same pair keep passing while this one fails, which is also what
        tells the agent the path is fine and the name is not.
        """
        source_node = self.nodes[source]
        target = hostname or destination_ip
        result = self.executor.run_shell(source_node, f"ping -c 3 -W 1 {target}", timeout_seconds=10)
        output = (result.stdout + result.stderr).strip()
        loss = self._extract_packet_loss(output)
        reachable = result.ok and loss == 0.0
        resolved_ip = self._extract_resolved_ip(output) if hostname else None
        # Resolving to another address than the topology declares is a failure of
        # the same kind as not resolving at all: the name no longer names the node.
        success = reachable and (hostname is None or resolved_ip == destination_ip)
        return ConnectivityCheck(
            source=source,
            destination=destination,
            destination_ip=destination_ip,
            success=success,
            packet_loss_percent=loss,
            output=output,
            hostname=hostname,
            resolved_ip=resolved_ip,
        )

    def _basic_raw_observations(self) -> dict[str, str]:
        raw: dict[str, str] = {}
        for node in self.nodes.values():
            if node.kind == "linux":
                result = self.executor.run_shell(node, "ip -brief address")
                raw[f"{node.name}:ip_brief_address"] = result.stdout or result.stderr
            elif node.kind == "nokia_srlinux":
                result = self.executor.run_srl_cli(node, "show version")
                raw[f"{node.name}:show_version"] = result.stdout or result.stderr
            elif node.kind == "vyos":
                result = self.executor.run_vyos_op(node, "show interfaces")
                raw[f"{node.name}:show_interfaces"] = result.stdout or result.stderr
        return raw

    @staticmethod
    def _extract_resolved_ip(output: str) -> str | None:
        """The address the name resolved to, or None when it did not resolve.

        A resolver that is gone, unreachable or wrong makes `ping` fail before it
        sends anything -- `bad address`, `Temporary failure in name resolution` --
        and no address is echoed.
        """
        match = _RESOLVED_IP_RE.search(output)
        return match.group("ip") if match else None

    @staticmethod
    def _extract_packet_loss(output: str) -> float:
        match = _PACKET_LOSS_RE.search(output)
        if not match:
            return 100.0
        return float(match.group("loss"))
