from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LabNode:
    """A node known by the ContainerLab topology."""

    name: str
    kind: str
    container_name: str


@dataclass(frozen=True)
class CommandResult:
    """Result of one action executed against the lab."""

    target: str
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    safe: bool = True
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ConnectivityCheck:
    """One reachability check between two lab nodes.

    With ``hostname`` set the check was run against a name instead of an
    address, so it exercises the resolver as well as the path: it is the only
    observation that can tell a broken zone, a missing record or a misprovisioned
    resolver apart from a healthy network. ``resolved_ip`` is what the name
    actually answered, which is compared against ``destination_ip``.
    """

    source: str
    destination: str
    destination_ip: str
    success: bool
    packet_loss_percent: float
    output: str
    hostname: str | None = None
    resolved_ip: str | None = None


@dataclass(frozen=True)
class Observation:
    """State summary sent to the benchmark or SUT."""

    lab_name: str
    nodes: list[LabNode]
    connectivity: list[ConnectivityCheck] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Render a compact text context for an A2A task."""
        lines = [f"Lab: {self.lab_name}", "Nodes:"]
        for node in self.nodes:
            lines.append(f"- {node.name} ({node.kind}) as {node.container_name}")
        if self.connectivity:
            lines.append("Connectivity checks:")
            for check in self.connectivity:
                status = "OK" if check.success else "FAILED"
                if check.hostname:
                    resolved = check.resolved_ip or "unresolved"
                    target = (
                        f"{check.hostname} -> {resolved}, "
                        f"expected {check.destination_ip}"
                    )
                else:
                    target = check.destination_ip
                lines.append(
                    f"- {check.source} -> {check.destination} "
                    f"({target}): {status}, "
                    f"loss={check.packet_loss_percent}%"
                )
        if self.raw:
            lines.append("Raw observations:")
            for key, value in self.raw.items():
                lines.append(f"[{key}]\n{value.strip()}")
        return "\n".join(lines)
