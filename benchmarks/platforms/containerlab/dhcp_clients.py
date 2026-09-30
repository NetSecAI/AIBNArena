"""The DHCP clients of a topology, and how to make one ask its server again.

Shared by the fault injector, the judge and the ANI: all three move the client's
clock rather than wait for it. A lease here lasts 3600 s and udhcpc renews at T1,
half of that, which is the whole episode budget; a client left alone keeps
whatever the lease under the fault said until the episode is over, so a
server-side change is invisible on the desk until someone renews.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

#: Where the healthy state's udhcpc keeps its pid; the desk has one DHCP port.
UDHCPC_PIDFILE = "/var/run/udhcpc.eth1.pid"
DHCP_INTERFACE = "eth1"
#: How long a renewal waits for an address before reporting what it saw. Under a
#: fault that removed the pool no address ever comes, and the wait ends by the clock.
RENEWAL_WAIT_SECONDS = 15


def dhcp_client_nodes(topology_document: Mapping[str, Any] | None) -> list[str]:
    """Nodes the descriptor says take their address from DHCP, in name order."""
    nodes = ((topology_document or {}).get("topology") or {}).get("nodes") or {}
    return sorted(
        name for name, node in nodes.items()
        if isinstance(node, Mapping) and node.get("address_assignment") == "dhcp"
    )


def dhcp_renew_shell(*, interface: str = DHCP_INTERFACE,
                     wait_seconds: int = RENEWAL_WAIT_SECONDS) -> str:
    """Release, renew, and wait for an address, as one shell command on the client.

    Releasing flushes the address through the hook's `deconfig` branch; renewing
    restarts the discovery a second later, so the desk is provisioned by the
    configuration as it stands now. /etc/resolv.conf is truncated first because
    the hook only rewrites it when the lease actually carries option 6: without
    this, a lease that stopped advertising a resolver would leave the previous one
    in place and the fault would be invisible on the desk. The wait that follows
    is what turns "the client was told to ask" into "the client has answered or
    had its chance": a probe run in the gap between release and the new lease
    measured the gap, not the network. Always exits 0: a client that gets no
    address is a fact the probes report, not a failed renewal.
    """
    return (
        f"sh -c 'P=$(cat {UDHCPC_PIDFILE} 2>/dev/null); "
        ": > /etc/resolv.conf; "
        'if [ -n "$P" ]; then kill -USR2 $P; sleep 2; kill -USR1 $P; fi; '
        f"i=0; while [ $i -lt {int(wait_seconds)} ]; do "
        f"ip -4 addr show {interface} 2>/dev/null | grep -q \" inet \" && break; "
        "i=$((i+1)); sleep 1; done; exit 0'"
    )


def renew_dhcp_clients(topology_document: Mapping[str, Any] | None,
                       run: Callable[[str, str], Any]) -> dict[str, Any]:
    """Renew every DHCP client of the topology; what each one reported.

    `run(client, command)` executes a shell command on a node and returns an
    object with `ok` and `returncode`. A topology without DHCP clients renews
    nothing and says so with an empty list rather than being skipped in silence.
    """
    outcomes = []
    for client in dhcp_client_nodes(topology_document):
        try:
            result = run(client, dhcp_renew_shell())
        except Exception as exc:  # noqa: BLE001 - one client must not hide the others
            outcomes.append({"client": client, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        outcomes.append({"client": client, "ok": bool(getattr(result, "ok", False)),
                         "returncode": getattr(result, "returncode", None)})
    return {"clients": outcomes}
