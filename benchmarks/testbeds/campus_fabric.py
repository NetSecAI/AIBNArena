"""The campus labs' switches, as Arista EOS configuration rendered from their descriptor.

    access switch ══ Port-Channel1 (LACP) ══╦══ switch-aggregate-1 ═╗
                                            ╚══ switch-aggregate-2 ═╩══ bond0 ── firewall
                                                 └─ Port-Channel999 ─┘
                                                    MLAG peer-link

The fabric: every access switch bundles one uplink to
each aggregate into a single LACP port-channel, and the two aggregates answer it
as one switch -- an MLAG domain over a peer-link -- so that bundle, and
the firewall's bond0 over the same pair, are one logical link each with two
cables under it. A cable or a whole aggregate can go without a VLAN losing its
path. campus_small has no aggregates and campus_large's building C a single one;
there a trunk is a plain port, and nothing else changes.

Every switch is pure layer 2: the only address on one is the MLAG peer SVI
(VLAN 4094), which carries the peers' keepalives on the peer-link and nothing
else. The firewall is the gateway of every LAN VLAN, and a switch that routed
would be a way around it.

Nothing is spelled out here. The descriptor declares each switch's interfaces --
a port-channel with its `members` and its `mlag` id, a plain trunk port, an
access port -- and the segments say what each carries: a trunk's
`tagged_vlans`, an access port's `vlan_id`, the peer-link's domain and peer
addresses. So the declaration the compiler reasons over and the configuration the
lab holds cannot drift apart.
"""
from __future__ import annotations

from typing import Any

#: The segment type of an MLAG peer-link: the two aggregates, the VLAN their
#: keepalives ride, the domain they form.
PEER_LINK = "mlag_peer_link"
#: The trunk group that keeps the peer VLAN on the peer-link alone.
PEER_TRUNK_GROUP = "MLAGPEER"


def vlan_name(vlan_id: int) -> str:
    """The name every switch gives a VLAN it carries.

    One spelling for the healthy state and for the restore of a deleted VLAN
    (compiled_topology._render_vlan_operation): a VLAN recreated under another name is
    a configuration the healthy state never held.
    """
    return f"VLAN_{vlan_id}"


def _command(target: str, command: str) -> dict[str, str]:
    return {"target": target, "mode": "eos_cli", "command": command}


def switches(topology: dict[str, Any]) -> list[str]:
    """The nodes this module renders: every Arista switch, in name order."""
    return sorted(name for name, spec in topology["nodes"].items() if spec["kind"] == "arista_ceos")


def peer_link(topology: dict[str, Any], node: str) -> tuple[str, dict[str, Any]] | None:
    """The MLAG peer-link segment ``node`` sits on, or None for a standalone switch."""
    for name, segment in topology["segments"].items():
        if segment.get("type") == PEER_LINK and node in segment["members"]:
            return name, segment
    return None


def _vlans(topology: dict[str, Any], node: str) -> set[int]:
    vlans: set[int] = set()
    for interface in topology["nodes"][node]["interfaces"].values():
        segment = topology["segments"][interface["segment"]]
        if segment["type"] == "trunk":
            vlans.update(segment["tagged_vlans"])
        elif segment["type"] == "vlan":
            vlans.add(segment["vlan_id"])
    return vlans


def _far_ends(topology: dict[str, Any], node: str, segment: str) -> str:
    """Who the port leads to: the segment's other switches and firewalls.

    An aggregate's MLAG peer sits on every bundle the pair terminates, at the same
    end as the aggregate itself, so it is not what the bundle leads to.
    """
    peer = peer_link(topology, node)
    same_end = set(peer[1]["members"]) if peer else {node}
    members = [member for member in topology["segments"][segment]["members"] if member not in same_end]
    return ", ".join(members)


def fabric_commands(topology: dict[str, Any], node: str) -> list[dict[str, str]]:
    """Everything ``node`` holds, as EOS configuration lines in session order.

    Each line applies in the mode the lines before it entered, so the order is the
    configuration's own: VLANs, then each port-channel followed by its members, then
    the plain ports, then the MLAG peer SVI and domain.
    """
    spec = topology["nodes"][node]
    if spec["kind"] != "arista_ceos":
        raise ValueError(f"{node}: campus_fabric renders arista_ceos switches, not {spec['kind']!r}")
    segments = topology["segments"]
    peer = peer_link(topology, node)
    lines: list[str] = []

    for vlan in sorted(_vlans(topology, node)):
        lines += [f"vlan {vlan}", f"name {vlan_name(vlan)}"]
    if peer:
        _, link = peer
        lines += [f"vlan {link['vlan_id']}", "name MLAG_PEER", f"trunk group {PEER_TRUNK_GROUP}",
                  f"no spanning-tree vlan-id {link['vlan_id']}"]

    for name, interface in spec["interfaces"].items():
        segment = segments[interface["segment"]]
        if name.startswith("Port-Channel"):
            channel = name.removeprefix("Port-Channel")
            if segment["type"] == PEER_LINK:
                other = next(member for member in segment["members"] if member != node)
                lines += [f"interface {name}", f"description MLAG peer-link to {other}",
                          "switchport mode trunk", f"switchport trunk group {PEER_TRUNK_GROUP}"]
            else:
                lines += [f"interface {name}",
                          f"description {interface.get('description') or 'Trunk to ' + _far_ends(topology, node, interface['segment'])}",
                          "switchport mode trunk",
                          "switchport trunk allowed vlan " + ",".join(str(v) for v in segment["tagged_vlans"])]
                if interface.get("mlag") is not None:
                    lines.append(f"mlag {interface['mlag']}")
            lines.append("no shutdown")
            for member in interface["members"]:
                lines += [f"interface {member}", f"description Member of {name}",
                          f"channel-group {channel} mode active", "no shutdown"]
        elif segment["type"] == "trunk":
            lines += [f"interface {name}",
                      f"description {interface.get('description') or 'Trunk to ' + _far_ends(topology, node, interface['segment'])}",
                      "switchport mode trunk",
                      "switchport trunk allowed vlan " + ",".join(str(v) for v in segment["tagged_vlans"]),
                      "no shutdown"]
        elif segment["type"] == "vlan":
            endpoint = interface.get("attached_endpoint")
            lines += [f"interface {name}",
                      f"description {interface.get('description') or 'Access port to ' + str(endpoint)}",
                      "switchport mode access", f"switchport access vlan {segment['vlan_id']}",
                      # No `spanning-tree portfast`: EOS's default (`portfast auto`)
                      # already makes a port that hears no BPDU an edge port within
                      # seconds, and the explicit line only adds a caution to every
                      # session that applies it.
                      "no shutdown"]
        else:
            raise ValueError(f"{node}:{name}: segment type {segment['type']!r} has no switch rendering")

    if peer:
        _, link = peer
        local = link["peer_addresses"][node]
        remote = next(address for member, address in link["peer_addresses"].items() if member != node)
        svi = f"Vlan{link['vlan_id']}"
        channel = next(name for name, interface in spec["interfaces"].items()
                       if segments[interface["segment"]]["type"] == PEER_LINK)
        lines += [f"interface {svi}", "description MLAG peer keepalives", "no autostate",
                  f"ip address {local}", "no shutdown",
                  "mlag configuration", f"domain-id {link['domain_id']}", f"local-interface {svi}",
                  f"peer-address {remote.split('/')[0]}", f"peer-link {channel}"]
    return [_command(node, line) for line in lines]
