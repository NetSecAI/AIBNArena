"""The remote site every campus lab reaches over a shaped WAN, rendered from its descriptor.

    LAN firewall ──/31── router-wan ──/31── wan-edge ══ 10 Mbit ══ srv-site
                         VyOS               Linux, tc on eth2      iperf3 sinks

campus_small, campus_medium and campus_large hang the same leg off the firewall
that is their LAN gateway, and it is there for two scenario families the campus
alone could not carry:

  routes  wrong_routing_table rewrites the route a router holds toward a remote
          destination. The LAN firewall routes the site's prefix explicitly,
          through router-wan, a second router a loop can be made with (m4); and
          its default route leads elsewhere, so a route made unusable (m3) is not
          rescued by it.
  QoS     wan-edge's eth2 is the bottleneck the descriptor's `qos_policy` shapes,
          srv-site the sink behind it, so the shaped scenarios have a circuit to
          contend for.

Nothing is spelled out here: addresses, routes, the shaping policy and the iperf3
sinks are read from the descriptor, so the declaration the compiler binds a fault
to and the state the lab holds cannot drift apart. The LAN firewall's side -- its
port, its zone, its rulesets -- belongs to each lab's generator.
"""
from __future__ import annotations

from typing import Any

from benchmarks.platforms.containerlab.compiled_topology import (
    render_qos_circuit_commands,
    render_qos_policy_commands,
)

ROUTER, EDGE, SINK = "router-wan", "wan-edge", "srv-site"

#: containerlab brings veths up at 9500. A jumbo endpoint behind a hop that does
#: not carry jumbo frames blackholes PMTU discovery, and every throughput
#: measurement across the circuit becomes meaningless (sme01-qos measured it).
MTU = 1500


def _command(target: str, mode: str, command: str) -> dict[str, str]:
    return {"target": target, "mode": mode, "command": command}


def static_route_commands(topology: dict[str, Any], node: str) -> list[dict[str, str]]:
    """The static routes ``node`` declares, as its platform writes them."""
    spec = topology["nodes"][node]
    commands = []
    for prefix, route in (spec.get("static_routes") or {}).items():
        hops = list(route.get("next_hops") or [])
        if spec["kind"] == "vyos":
            if route.get("blackhole"):
                commands.append(_command(node, "vyos_cli",
                                         f"set protocols static route {prefix} blackhole"))
            for hop in hops:
                commands.append(_command(node, "vyos_cli",
                                         f"set protocols static route {prefix} next-hop {hop}"))
        elif spec["kind"] == "linux":
            if route.get("blackhole"):
                line = f"ip route replace blackhole {prefix}"
            elif len(hops) == 1:
                line = f"ip route replace {prefix} via {hops[0]}"
            else:
                line = f"ip route replace {prefix} " + " ".join(f"nexthop via {hop}" for hop in hops)
            commands.append(_command(node, "shell", line))
        else:
            raise ValueError(f"{node}: no static-route rendering for kind {spec['kind']!r}")
    return commands


def _peer(topology: dict[str, Any], node: str, segment: str) -> str:
    members = topology["segments"][segment]["members"]
    others = [member for member in members if member != node]
    return others[0] if len(others) == 1 else segment


def _segment_gateway_address(topology: dict[str, Any], segment: str) -> str:
    for name, spec in topology["nodes"].items():
        if "gateway" not in spec.get("roles", []):
            continue
        for interface in spec.get("interfaces", {}).values():
            if interface.get("segment") == segment and interface.get("ipv4"):
                return interface["ipv4"].split("/")[0]
    raise ValueError(f"segment {segment!r} has no gateway address")


def site_commands(topology: dict[str, Any], *, with_qos_policy: bool) -> list[dict[str, str]]:
    """Everything the site leg holds, router-wan to srv-site.

    ``with_qos_policy`` False keeps the 10 Mbit circuit and drops only the class
    policy: the greenfield state assured_bandwidth starts from. Without the
    circuit there would be no bottleneck, and an intent asking for 8 Mbit would
    be met before the agent touched anything.
    """
    nodes = topology["nodes"]
    commands: list[dict[str, str]] = []

    # router-wan: a plain VyOS router, no zone. It drops containerlab's management
    # default route in topology.clab.yml, as the firewalls do. eth0 is declared
    # for the same reason as on them: a port absent from the configuration is one
    # a reader of the configuration cannot tell is management.
    commands.append(_command(ROUTER, "vyos_cli", "set interfaces ethernet eth0 description 'Management Interface'"))
    commands.append(_command(ROUTER, "vyos_cli", "set interfaces ethernet eth0 ipv6 address no-default-link-local"))
    for name, interface in sorted(nodes[ROUTER]["interfaces"].items()):
        peer = _peer(topology, ROUTER, interface["segment"])
        commands.append(_command(ROUTER, "vyos_cli", f"set interfaces ethernet {name} address {interface['ipv4']}"))
        commands.append(_command(ROUTER, "vyos_cli", f"set interfaces ethernet {name} description 'Link to {peer}'"))
    commands.extend(static_route_commands(topology, ROUTER))

    # wan-edge: a Linux router, which is what carries `tc`.
    for name, interface in sorted(nodes[EDGE]["interfaces"].items()):
        commands.append(_command(EDGE, "shell", f"ip link set {name} up"))
        commands.append(_command(EDGE, "shell", f"ip link set {name} mtu {MTU}"))
        commands.append(_command(EDGE, "shell", f"ip address replace {interface['ipv4']} dev {name}"))
    commands.append(_command(EDGE, "shell", "sysctl -w net.ipv4.ip_forward=1"))
    commands.extend(static_route_commands(topology, EDGE))
    render = render_qos_policy_commands if with_qos_policy else render_qos_circuit_commands
    commands.extend(_command(c.target, c.mode, c.command) for c in render(topology["qos_policy"]))

    # srv-site: the sink. One iperf3 server per declared iperf3 service, since one
    # server runs one test at a time and the throughput oracle measures against one
    # while it floods the other.
    (name, interface), = nodes[SINK]["interfaces"].items()
    commands.append(_command(SINK, "shell", f"ip link set {name} up"))
    commands.append(_command(SINK, "shell", f"ip link set {name} mtu {MTU}"))
    commands.append(_command(SINK, "shell", f"ip address replace {interface['ipv4']} dev {name}"))
    commands.append(_command(SINK, "shell", "ip route replace default via "
                                            f"{_segment_gateway_address(topology, interface['segment'])}"))
    ports = sorted(int(spec["port"]) for spec in topology.get("services", {}).values()
                   if spec.get("application") == "iperf3" and spec.get("node") == SINK)
    commands.append(_command(SINK, "shell", "sh -c 'pkill -x iperf3 || true'"))
    for port in ports:
        commands.append(_command(SINK, "shell", f"sh -c 'setsid iperf3 -s -p {port} >/dev/null 2>&1 </dev/null &'"))
    return commands
