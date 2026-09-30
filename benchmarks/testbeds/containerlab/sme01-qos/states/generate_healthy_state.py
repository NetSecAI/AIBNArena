#!/usr/bin/env python3
"""Generate the healthy reference states for the sme01-qos lab.

Same routed fabric as sme01-small -- routed access interfaces on the leaves,
static routes with ECMP through the spines -- plus a Linux WAN edge attached to
both spines, between the fabric and the simulated Internet.

Two states are emitted from this one script, because the lab serves two
scenario families that disagree about a single thing: whether the WAN edge
already carries its shaping policy.

    healthy.json             policy present   -> wan_shaping_policy_repair
    healthy_greenfield.json  policy absent    -> assured_bandwidth

They are generated together so they cannot drift apart, and the policy itself
is not spelled out here: it is rendered from the descriptor's ``qos_policy`` by
the same function the reference restore uses.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))

from benchmarks.platforms.containerlab.compiled_topology import (  # noqa: E402
    render_qos_circuit_commands,
    render_qos_policy_commands,
)
from scenarios.compiler.loader import load_yaml  # noqa: E402

OUT_DIR = REPO / "benchmarks/testbeds/containerlab/sme01-qos/states"
DESCRIPTOR = REPO / "scenarios/topologies/sme_wan_edge_qos.yaml"

# endpoint -> (address, default gateway)
ENDPOINTS = {
    "user1":     ("10.10.10.10/24", "10.10.10.1"),
    "guest1":    ("10.10.20.10/24", "10.10.20.1"),
    "finance1":  ("10.10.30.10/24", "10.10.30.1"),
    "web1":      ("10.10.40.10/24", "10.10.40.1"),
    "app1":      ("10.10.50.10/24", "10.10.50.1"),
    "admin1":    ("10.10.99.100/24", "10.10.99.1"),
    "external1": ("203.0.113.10/24", "203.0.113.1"),
}

# device -> interface -> address
FABRIC = {
    # ethernet-1/3 on each spine is that spine's uplink to the WAN edge.
    "spine1": {
        "ethernet-1/1": "10.255.0.0/31",
        "ethernet-1/2": "10.255.0.4/31",
        "ethernet-1/3": "10.255.1.0/31",
    },
    "spine2": {
        "ethernet-1/1": "10.255.0.2/31",
        "ethernet-1/2": "10.255.0.6/31",
        "ethernet-1/3": "10.255.1.2/31",
    },
    "leaf1": {
        "ethernet-1/1": "10.255.0.1/31",
        "ethernet-1/2": "10.255.0.3/31",
        "ethernet-1/10": "10.10.10.1/24",
        "ethernet-1/20": "10.10.20.1/24",
        "ethernet-1/57": "10.10.99.1/24",
    },
    "leaf2": {
        "ethernet-1/1": "10.255.0.5/31",
        "ethernet-1/2": "10.255.0.7/31",
        "ethernet-1/30": "10.10.30.1/24",
        "ethernet-1/40": "10.10.40.1/24",
        "ethernet-1/50": "10.10.50.1/24",
    },
}

# device -> next-hop-group name -> [next hops]
# Group names follow the descriptor's `to-{gateway}` template, where the gateway
# is the node that owns the destination segment -- not the next hop the traffic
# takes to get there. That is why both leaves reach the Internet through a group
# named `to-wan1` whose next hops are the two spines.
NEXT_HOP_GROUPS = {
    # The spines are the only devices directly attached to the WAN edge, one
    # uplink each.
    "spine1": {
        "to-leaf1": ["10.255.0.1"],
        "to-leaf2": ["10.255.0.5"],
        "to-wan1": ["10.255.1.1"],
    },
    "spine2": {
        "to-leaf1": ["10.255.0.3"],
        "to-leaf2": ["10.255.0.7"],
        "to-wan1": ["10.255.1.3"],
    },
    # Each leaf reaches the far leaf -- and now the WAN edge too -- through
    # either spine: two next hops, ECMP.
    "leaf1": {
        "to-leaf2": ["10.255.0.0", "10.255.0.2"],
        "to-wan1": ["10.255.0.0", "10.255.0.2"],
    },
    "leaf2": {
        "to-leaf1": ["10.255.0.4", "10.255.0.6"],
        "to-wan1": ["10.255.0.4", "10.255.0.6"],
    },
}

# device -> next-hop-group name -> prefixes routed through it
ROUTES = {
    # The spines hand the Internet prefix straight to the WAN edge; nothing
    # beyond it is reachable through a leaf any more.
    "spine1": {
        "to-leaf1": ["10.10.10.0/24", "10.10.20.0/24", "10.10.99.0/24"],
        "to-leaf2": ["10.10.30.0/24", "10.10.40.0/24", "10.10.50.0/24"],
        "to-wan1": ["203.0.113.0/24"],
    },
    "spine2": {
        "to-leaf1": ["10.10.10.0/24", "10.10.20.0/24", "10.10.99.0/24"],
        "to-leaf2": ["10.10.30.0/24", "10.10.40.0/24", "10.10.50.0/24"],
        "to-wan1": ["203.0.113.0/24"],
    },
    # Both leaves reach the WAN edge the same way now: up to either spine.
    "leaf1": {
        "to-leaf2": ["10.10.30.0/24", "10.10.40.0/24", "10.10.50.0/24"],
        "to-wan1": ["203.0.113.0/24"],
    },
    "leaf2": {
        "to-leaf1": ["10.10.10.0/24", "10.10.20.0/24", "10.10.99.0/24"],
        "to-wan1": ["203.0.113.0/24"],
    },
}

WAN_EDGE = {
    "node": "wan1",
    "interfaces": {
        # eth1 and eth3 are the two spine uplinks. eth2 is the shaped
        # Internet-facing port and keeps that name because the descriptor's
        # qos_policy is the single spelling every renderer and fault shares.
        "eth1": "10.255.1.1/31",
        "eth3": "10.255.1.3/31",
        "eth2": "203.0.113.1/24",
    },
    # One summary back into the campus rather than a route per VLAN, spread over
    # both uplinks: the WAN edge only ever needs to hand return traffic to the
    # fabric, and either spine will carry it.
    "return_route": (
        "10.10.0.0/16",
        (("10.255.1.0", "eth1"), ("10.255.1.2", "eth3")),
    ),
}


def build(with_qos_policy: bool):
    commands = []

    def add(target, mode, command):
        commands.append({"target": target, "mode": mode, "command": command})

    def srl(target, command):
        add(target, "srl_cli", command)

    # --- endpoints ---------------------------------------------------------
    for host, (address, gateway) in ENDPOINTS.items():
        add(host, "shell", "ip link set eth1 up")
        # containerlab brings veths up at 9500. A jumbo endpoint behind a fabric
        # that does not carry jumbo frames blackholes PMTU discovery: ping still
        # works, TCP collapses to a few hundred kbit/s, and every throughput
        # measurement in this lab becomes meaningless.
        add(host, "shell", "ip link set eth1 mtu 1500")
        add(host, "shell", f"ip address replace {address} dev eth1")
        add(host, "shell", f"ip route replace default via {gateway}")

    # One iperf3 server per declared iperf3 service, so the sinks follow the
    # descriptor rather than a list kept in step by hand. A server handles one
    # test at a time, which is why the WAN sink declares two ports: one for the
    # measured flow, one for the evaluator's background load.
    descriptor = load_yaml(DESCRIPTOR)["topology"]
    sinks = sorted(
        {
            (spec["node"], int(spec["port"]))
            for spec in descriptor.get("services", {}).values()
            if spec.get("application") == "iperf3"
        }
    )
    for host in sorted({node for node, _ in sinks}):
        add(host, "shell", "sh -c 'pkill -x iperf3 || true'")
    for host, port in sinks:
        add(
            host,
            "shell",
            f"sh -c 'setsid iperf3 -s -p {port} >/dev/null 2>&1 </dev/null &'",
        )

    # --- WAN edge ----------------------------------------------------------
    edge = WAN_EDGE["node"]
    for interface, address in WAN_EDGE["interfaces"].items():
        add(edge, "shell", f"ip link set {interface} up")
        add(edge, "shell", f"ip link set {interface} mtu 1500")
        add(edge, "shell", f"ip address replace {address} dev {interface}")
    add(edge, "shell", "sysctl -w net.ipv4.ip_forward=1")
    prefix, next_hops = WAN_EDGE["return_route"]
    # iproute2 multipath syntax, one clause per spine uplink. run_shell execs
    # after shlex.split with no shell, and this needs no shell grammar.
    hops = " ".join(f"nexthop via {via} dev {name}" for via, name in next_hops)
    add(edge, "shell", f"ip route replace {prefix} {hops}")

    policy = descriptor["qos_policy"]
    # Greenfield keeps the circuit -- the uplink really is 10 Mbps -- and drops
    # only the class policy. Without the circuit there would be no bottleneck at
    # all, the fabric would carry some 65 Mbps end to end, and an intent asking
    # for 8 Mbps would be satisfied before the agent touched anything.
    render = render_qos_policy_commands if with_qos_policy else render_qos_circuit_commands
    for command in render(policy):
        add(command.target, command.mode, command.command)

    # --- fabric ------------------------------------------------------------
    for device, interfaces in FABRIC.items():
        for interface, address in interfaces.items():
            srl(device, f"set / interface {interface} admin-state enable")
            srl(device, f"set / interface {interface} subinterface 0 admin-state enable")
            srl(device, f"set / interface {interface} subinterface 0 ipv4 admin-state enable")
            srl(device, f"set / interface {interface} subinterface 0 ipv4 address {address}")
            srl(device, f"set / network-instance default interface {interface}.0")

        for group, next_hops in NEXT_HOP_GROUPS.get(device, {}).items():
            for index, next_hop in enumerate(next_hops, start=1):
                srl(
                    device,
                    f"set / network-instance default next-hop-groups group {group} "
                    f"nexthop {index} ip-address {next_hop} admin-state enable",
                )
            for prefix in ROUTES.get(device, {}).get(group, []):
                srl(
                    device,
                    f"set / network-instance default static-routes route {prefix} "
                    f"next-hop-group {group} admin-state enable",
                )

    return commands


def write(path, identifier, description, commands):
    path.write_text(
        json.dumps(
            {"id": identifier, "description": description, "commands": commands},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    modes = {}
    for command in commands:
        modes[command["mode"]] = modes.get(command["mode"], 0) + 1
    print(f"{path}: {len(commands)} commands ({modes})")


SHARED = (
    "Routed leaf-spine fabric with a Linux WAN edge attached to both spines, between "
    "the fabric and the simulated "
    "Internet. Endpoint interfaces are pinned to MTU 1500 because containerlab brings "
    "them up at 9500, which blackholes PMTU discovery across the fabric and makes every "
    "throughput measurement meaningless. app1 and external1 run an iperf3 server so a "
    "throughput probe has a sink."
)

if __name__ == "__main__":
    write(
        OUT_DIR / "healthy.json",
        "sme01_qos_shaped_wan_edge_healthy",
        SHARED
        + " wan1:eth2 carries the reference two-class HTB policy declared by the "
        "topology descriptor: business traffic is guaranteed 8 Mbps of the 10 Mbps "
        "uplink, everything else 2 Mbps, and both may burst to the whole link when it "
        "is idle. This is the state the shaping-repair scenarios degrade.",
        build(with_qos_policy=True),
    )
    write(
        OUT_DIR / "healthy_greenfield.json",
        "sme01_qos_unshaped_wan_edge_healthy",
        SHARED
        + " wan1:eth2 carries no shaping policy at all: the uplink is first come, "
        "first served. This is the state the assured-bandwidth scenario starts from, "
        "where the agent has to design the policy rather than repair one.",
        build(with_qos_policy=False),
    )
