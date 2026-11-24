"""Measure a change request on the live lab: a VLAN isolation, a service publication.

A change request that moves hosts into a VLAN of their own also moves them to a new
subnet: the addresses the topology descriptor declares for them are the ones they had
before the change. Every measurement here therefore reads the addresses a destination
holds at the moment it is measured, on its data-plane interfaces, rather than the
declared one. The judge's oracles (`ContainerlabPlatform`) and the subject's own public
criterion (`ContainerLabANI`) measure through the same functions, so what the subject
validates is what it is scored on.

Three readings, one per question the intent asks:

- reach: the destination answers on at least one of its addresses;
- reach with a TTL of 1: same, without crossing a router, which is what sharing one
  broadcast domain means for a ping (a router drops the packet and answers "time to
  live exceeded");
- isolation: the destination answers on none of its addresses. A destination that
  holds no data-plane address cannot be reached and counts as isolated.

A service publication asks the same questions over TCP: the service answers on its port
(`tcp_reach`), and no other port the host listens on answers (`tcp_isolation`, over the
ports `listening_tcp_ports` reads on the host itself, so a rule opened wider than the
service shows up wherever something actually listens).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .types import CommandResult


#: Every IPv4 address of the node, one line each.
ADDRESS_COMMAND = "ip -4 -o address show"

#: The interface containerlab gives every node for its management network. Nothing
#: measured here may go through it: two nodes always reach each other over it.
MANAGEMENT_INTERFACE = "eth0"

_INET = re.compile(r"^\d+:\s+(?P<device>[^\s@]+)(?:@\S+)?\s+inet\s+(?P<address>[0-9]+(?:\.[0-9]+){3})/")
_LOSS = re.compile(r"(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s+packet loss")

#: (node, command, timeout seconds) -> result of running the command on the node.
Runner = Callable[[str, str, float], CommandResult]

#: Every TCP socket of the node, IPv4 and IPv6, as the kernel lists them. Read from /proc
#: rather than with ss or netstat: the bind9 image of the DMZ resolver has neither.
LISTENING_COMMAND = "cat /proc/net/tcp /proc/net/tcp6"
_TCP_LISTEN = "0A"
#: Loopback local addresses in /proc/net/tcp{,6} (hex, as the kernel writes them).
_LOOPBACK_V6 = "00000000000000000000000001000000"


def parse_paths(paths: Any) -> list[tuple[str, str]]:
    """`["a->b", ...]` as `(source, destination)` pairs, in order, once each."""
    if not isinstance(paths, (list, tuple)):
        raise ValueError(f"paths must be a list of 'source->destination', not {type(paths).__name__}")
    pairs: list[tuple[str, str]] = []
    for item in paths:
        source, separator, destination = str(item).partition("->")
        if not separator or not source.strip() or not destination.strip():
            raise ValueError(f"path {item!r} is not 'source->destination'")
        pair = (source.strip(), destination.strip())
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def data_plane_addresses(output: str) -> list[str]:
    """The IPv4 addresses `ip -4 -o address show` lists outside lo and the management
    interface, in the order listed."""
    addresses: list[str] = []
    for line in output.splitlines():
        match = _INET.match(line.strip())
        if not match or match.group("device") in {"lo", MANAGEMENT_INTERFACE}:
            continue
        if match.group("address") not in addresses:
            addresses.append(match.group("address"))
    return addresses


def live_addresses(run: Runner, node: str) -> list[str]:
    """The data-plane addresses ``node`` holds now. Raises when they cannot be read:
    an unread destination must never be scored as one that holds no address."""
    result = run(node, ADDRESS_COMMAND, 10)
    if not result.ok:
        raise RuntimeError(
            f"could not read the addresses of {node}: return code {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[-200:]}")
    return data_plane_addresses(result.stdout)


def ping(run: Runner, source: str, address: str, *, attempts: int = 3,
         timeout_seconds: float = 1, ttl: int | None = None) -> dict[str, Any]:
    """One ping from ``source`` to ``address``: success means no packet lost."""
    hops = f" -t {int(ttl)}" if ttl else ""
    command = f"ping -q -c {int(attempts)} -W {max(1, int(timeout_seconds))}{hops} {address}"
    result = run(source, command, max(30, int(attempts) * (float(timeout_seconds) + 1) + 5))
    match = _LOSS.search(f"{result.stdout}{result.stderr}")
    loss = float(match.group("loss")) if match else 100.0
    return {"address": address, "success": result.ok and loss == 0.0, "packet_loss_percent": loss}


def reach(run: Runner, source: str, destination: str, addresses: Iterable[str], *,
          attempts: int = 3, timeout_seconds: float = 1, ttl: int | None = None) -> dict[str, Any]:
    """Whether ``destination`` answers ``source`` on one of ``addresses``, tried in order."""
    addresses = list(addresses)
    tried = []
    for address in addresses:
        tried.append(ping(run, source, address, attempts=attempts,
                          timeout_seconds=timeout_seconds, ttl=ttl))
        if tried[-1]["success"]:
            break
    answered = next((item["address"] for item in tried if item["success"]), None)
    return {
        "source": source,
        "destination": destination,
        "addresses": addresses,
        "answered_address": answered,
        "success": answered is not None,
        "packet_loss_percent": min((item["packet_loss_percent"] for item in tried), default=100.0),
        **({"ttl": int(ttl)} if ttl else {}),
        **({} if addresses else {"reason": f"{destination} holds no data-plane IPv4 address"}),
    }


def isolation(run: Runner, source: str, destination: str, addresses: Iterable[str], *,
              attempts: int = 3, timeout_seconds: float = 1) -> dict[str, Any]:
    """Whether ``destination`` answers ``source`` on none of ``addresses``. A single
    reply on any of them is a leak: an isolation that lets one packet in three
    through is open."""
    addresses = list(addresses)
    tried = [ping(run, source, address, attempts=attempts, timeout_seconds=timeout_seconds)
             for address in addresses]
    leaked = [item["address"] for item in tried if item["packet_loss_percent"] < 100.0]
    return {
        "source": source,
        "destination": destination,
        "addresses": addresses,
        "leaked_addresses": leaked,
        "leaked": bool(leaked),
        "packet_loss_percent": min((item["packet_loss_percent"] for item in tried), default=100.0),
        **({} if addresses else {"reason": f"{destination} holds no data-plane IPv4 address"}),
    }


def measure_intent(run: Runner, spec: dict[str, Any], declared_address: Callable[[str], str], *,
                   attempts: int = 3, timeout_seconds: float = 1) -> dict[str, Any]:
    """The whole intent, as the subject's public criterion states it.

    ``spec`` names the paths: `reachable_paths` and `same_segment_paths` must answer
    (the latter with a TTL of 1), `isolated_paths` must not, all on live addresses;
    `preserved_paths` must answer on the address the descriptor declares, because the
    change leaves those hosts where they were.
    """
    cache: dict[str, list[str]] = {}

    def addresses(node: str) -> list[str]:
        if node not in cache:
            cache[node] = live_addresses(run, node)
        return cache[node]

    reachable = [reach(run, source, destination, addresses(destination),
                       attempts=attempts, timeout_seconds=timeout_seconds)
                 for source, destination in parse_paths(spec.get("reachable_paths") or [])]
    same_segment = [reach(run, source, destination, addresses(destination),
                          attempts=attempts, timeout_seconds=timeout_seconds, ttl=1)
                    for source, destination in parse_paths(spec.get("same_segment_paths") or [])]
    isolated = [isolation(run, source, destination, addresses(destination),
                          attempts=attempts, timeout_seconds=timeout_seconds)
                for source, destination in parse_paths(spec.get("isolated_paths") or [])]
    preserved = [reach(run, source, destination, [declared_address(destination)],
                       attempts=attempts, timeout_seconds=timeout_seconds)
                 for source, destination in parse_paths(spec.get("preserved_paths") or [])]
    passed = (
        all(item["success"] for item in reachable)
        and all(item["success"] for item in same_segment)
        and not any(item["leaked"] for item in isolated)
        and all(item["success"] for item in preserved)
        and bool(reachable or same_segment or isolated)
    )
    return {
        "passed": passed,
        "reachable": reachable,
        "same_segment": same_segment,
        "isolated": isolated,
        "preserved": preserved,
        "summary": {
            "reachable_failed": sum(not item["success"] for item in reachable),
            "same_segment_failed": sum(not item["success"] for item in same_segment),
            "isolated_leaked": sum(bool(item["leaked"]) for item in isolated),
            "preserved_failed": sum(not item["success"] for item in preserved),
        },
    }


def listening_ports(output: str) -> list[int]:
    """The TCP ports listening on a non-loopback address in `cat /proc/net/tcp /proc/net/tcp6`."""
    ports: set[int] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4 or ":" not in fields[1] or fields[3] != _TCP_LISTEN:
            continue
        address, _, port = fields[1].rpartition(":")
        if len(address) == 8 and address.endswith("7F"):  # 127.0.0.0/8, little-endian
            continue
        if address == _LOOPBACK_V6:
            continue
        try:
            ports.add(int(port, 16))
        except ValueError:
            continue
    return sorted(ports)


def listening_tcp_ports(run: Runner, node: str) -> list[int]:
    """The TCP ports ``node`` listens on now, loopback aside. Raises when they cannot be
    read: an unread host must never be scored as one that listens on nothing."""
    result = run(node, LISTENING_COMMAND, 10)
    if not result.ok:
        raise RuntimeError(
            f"could not read the listening ports of {node}: return code {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[-200:]}")
    return listening_ports(result.stdout)


def tcp_connect(run: Runner, source: str, address: str, port: int, *, attempts: int = 3,
                timeout_seconds: float = 1) -> dict[str, Any]:
    """`attempts` TCP connections from ``source`` to ``address``:``port``; how many opened."""
    command = f"nc -z -w {max(1, int(timeout_seconds))} {address} {int(port)}"
    opened = sum(
        run(source, command, float(timeout_seconds) + 5).ok for _ in range(max(1, int(attempts))))
    return {"address": address, "port": int(port), "attempts": max(1, int(attempts)), "opened": opened}


def tcp_reach(run: Runner, source: str, destination: str, addresses: Iterable[str], port: int, *,
              attempts: int = 3, timeout_seconds: float = 1) -> dict[str, Any]:
    """Whether every connection from ``source`` to ``port`` of ``destination`` opens, on one
    of ``addresses`` tried in order."""
    addresses = list(addresses)
    tried = []
    for address in addresses:
        tried.append(tcp_connect(run, source, address, port, attempts=attempts, timeout_seconds=timeout_seconds))
        if tried[-1]["opened"] == tried[-1]["attempts"]:
            break
    answered = next((item["address"] for item in tried if item["opened"] == item["attempts"]), None)
    return {
        "source": source, "destination": destination, "port": int(port), "addresses": addresses,
        "answered_address": answered, "success": answered is not None,
        **({} if addresses else {"reason": f"{destination} holds no data-plane IPv4 address"}),
    }


def tcp_isolation(run: Runner, source: str, destination: str, addresses: Iterable[str],
                  ports: Iterable[int], *, attempts: int = 3, timeout_seconds: float = 1) -> dict[str, Any]:
    """Whether no connection from ``source`` opens on any of ``ports`` of ``destination``, on any
    of ``addresses``. One connection opened is a leak."""
    addresses, ports = list(addresses), sorted({int(port) for port in ports})
    leaked = [
        f"{address}:{port}"
        for address in addresses for port in ports
        if tcp_connect(run, source, address, port, attempts=attempts, timeout_seconds=timeout_seconds)["opened"]
    ]
    return {
        "source": source, "destination": destination, "addresses": addresses, "ports": ports,
        "leaked_endpoints": leaked, "leaked": bool(leaked),
        **({} if ports else {"reason": f"{destination} listens on no TCP port to close"}),
    }


def parse_services(services: Any) -> dict[str, set[int]]:
    """`["web-dmz:80", ...]` as {node: {ports}}: the ports that are meant to answer."""
    if not isinstance(services, (list, tuple)):
        raise ValueError(f"open services must be a list of 'node:port', not {type(services).__name__}")
    opened: dict[str, set[int]] = {}
    for item in services:
        node, separator, port = str(item).rpartition(":")
        if not separator or not node or not port.isdigit():
            raise ValueError(f"open service {item!r} is not 'node:port'")
        opened.setdefault(node, set()).add(int(port))
    return opened


def measure_publication(run: Runner, spec: dict[str, Any], declared_address: Callable[[str], str], *,
                        attempts: int = 3, timeout_seconds: float = 1) -> dict[str, Any]:
    """A service publication, as the subject's public criterion states it.

    ``spec`` names the `source` outside, the `service` host and its `port`, and the
    `closed_hosts` of the service's zone. The service must answer the source on its port;
    on every closed host, no other TCP port it listens on may answer the source, nor may
    a ping. Addresses are the ones the descriptor declares: publishing moves nothing.
    """
    source, service, port = str(spec["source"]), str(spec["service"]), int(spec["port"])
    published = tcp_reach(run, source, service, [declared_address(service)], port,
                          attempts=attempts, timeout_seconds=timeout_seconds)
    closed_ports, closed_icmp = [], []
    for host in [str(item) for item in spec.get("closed_hosts") or []]:
        address = declared_address(host)
        ports = [item for item in listening_tcp_ports(run, host) if not (host == service and item == port)]
        closed_ports.append(tcp_isolation(run, source, host, [address], ports,
                                          attempts=attempts, timeout_seconds=timeout_seconds))
        closed_icmp.append(isolation(run, source, host, [address],
                                     attempts=attempts, timeout_seconds=timeout_seconds))
    passed = (published["success"] and not any(item["leaked"] for item in closed_ports)
              and not any(item["leaked"] for item in closed_icmp))
    return {
        "passed": passed,
        "published": published,
        "closed_ports": closed_ports,
        "closed_icmp": closed_icmp,
        "summary": {
            "published": published["success"],
            "ports_leaked": sum(len(item["leaked_endpoints"]) for item in closed_ports),
            "pings_leaked": sum(bool(item["leaked"]) for item in closed_icmp),
        },
    }
