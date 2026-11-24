"""What makes the lab's devices learn their neighbours' addresses.

`prime_linux_nodes` and `prime_resolvers` send traffic, so they run only where nothing
measures: the judge calls them before the subject (`prime_before_sut`) and at the end
(`post_repair`), and since 2026-11-20 the ANI for a subject's own code
(`ContainerLabANI.prime_learned_tables`). Moved here from
`platform.py`, unchanged, so that the ANI and the platform run the same code: `platform.py`
imports the ANI, which could not import them back from it.
"""
from __future__ import annotations

import ipaddress
from typing import Any


#: What `docker exec` returns for a binary the image does not have.
COMMAND_NOT_FOUND = 127


def prime_linux_nodes(platform: Any) -> int:
    """Have every Linux node send one frame to each data-plane next hop it routes
    through, and return how many next hops were sent one.

    A Linux endpoint sends no LLDP, so where it is cabled is read from the neighbour that
    learnt its MAC address (a switch's MAC table, else a VyOS ARP table), and a neighbour
    learns nothing from a node that has not sent a frame. At the reference state nothing
    has, and in an episode the judge's probes reach only some of the nodes. A route through
    the management interface (the Docker bridge) teaches the lab devices nothing and is
    skipped.

    Every next hop, not only the default gateway's: campus_small's wan-edge routes
    10.0.0.0/16 through router-wan and has no data-plane default route, its only default
    going through eth0. Until 2026-11-16 it was primed toward nothing, so router-wan held
    its address only while traffic to srv-site was recent, primed read or not.

    The node forgets the next hop first, so that its first frame is an ARP request, from
    which the next hop learns the node's address. From a frame sent to a cached address
    it learns only by answering it, and a zone policy may drop the ping, never the ARP
    request. The resolvers' ubuntu/bind9 image has no ping, so they send a datagram from
    bash, which needs no route past the next hop: on a network the episode left broken no
    client's query reaches them (`prime_resolvers`, 2026-11-10).
    """
    env = platform.env
    primed = 0
    for name, node in sorted(env.config.nodes.items()):
        if node.kind != "linux":
            continue
        routes = env.executor.run_shell(node, "ip -o -4 route show")
        for hop in data_plane_next_hops(routes.stdout):
            env.executor.run_shell(node, f"ip neigh flush to {hop}")
            if env.executor.run_shell(node, f"ping -c 1 -W 1 {hop}").returncode == COMMAND_NOT_FOUND:
                env.executor.run_shell(node, f"bash -c 'echo > /dev/udp/{hop}/9'")
            primed += 1
    return primed


def data_plane_next_hops(routes: str) -> list[str]:
    """The IPv4 next hops in `ip -o -4 route show`, once each and in the order listed,
    without those reached through eth0, the management interface. A multipath route
    names several, each with its own `dev`. A token after `via` that is no IPv4 address
    is skipped: it would be pasted into a shell command."""
    hops: list[str] = []
    for line in routes.splitlines():
        tokens = line.split()
        for index, token in enumerate(tokens[:-1]):
            if token != "via":
                continue
            device = next((tokens[later + 1] for later in range(index + 2, len(tokens) - 1)
                           if tokens[later] == "dev"), None)
            hop = tokens[index + 1]
            if device in (None, "eth0") or hop in hops:
                continue
            try:
                ipaddress.IPv4Address(hop)
            except ValueError:
                continue
            hops.append(hop)
    return hops


def prime_resolvers(platform: Any) -> int:
    """Have every Linux node with a lab resolver ask it once for its domain's SOA.

    A client's query makes the resolver answer, and the switch or firewall facing
    it learns its MAC from that reply (campus labs, 2026-10-28). Only a node whose
    resolv.conf names a search domain queries -- a leased client, toward the resolver
    DHCP handed it, or a DMZ server, toward the one its reference state names -- so the
    policy allows every query, and the zone it asks for is one that resolver is
    authoritative for, so the answer comes at once.
    """
    env = platform.env
    queried = 0
    for name, node in sorted(env.config.nodes.items()):
        if node.kind != "linux":
            continue
        resolv = env.executor.run_shell(node, "cat /etc/resolv.conf").stdout
        domains = [fields[1] for fields in (line.split() for line in resolv.splitlines())
                   if len(fields) > 1 and fields[0] in ("search", "domain")]
        if not domains or domains[0] == ".":
            continue
        if env.executor.run_shell(node, f"dig +time=2 +tries=1 +short {domains[0]} SOA").ok:
            queried += 1
    return queried
