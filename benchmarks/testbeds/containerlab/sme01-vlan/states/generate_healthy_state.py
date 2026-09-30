#!/usr/bin/env python3
"""Generate the healthy reference state for the sme01-vlan lab.

Leaves are pure L2 (one mac-vrf per VLAN, untagged access ports, tagged
uplinks). Spines are L3: one IRB per VLAN in the default ip-vrf, with a
shared VRRP virtual address acting as the endpoint default gateway.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
OUT = REPO / "benchmarks/testbeds/containerlab/sme01-vlan/states/healthy.json"

# vlan_id -> (name, cidr prefix, leaf carrying the VLAN, access ports on that leaf)
VLANS = {
    10: dict(name="users",   subnet="10.10.10",  leaf="leaf1", access=["ethernet-1/10"]),
    20: dict(name="guest",   subnet="10.10.20",  leaf="leaf1", access=["ethernet-1/20", "ethernet-1/30"]),
    30: dict(name="finance", subnet="10.10.30",  leaf="leaf2", access=["ethernet-1/30"]),
    40: dict(name="dmz",     subnet="10.10.40",  leaf="leaf2", access=["ethernet-1/40"]),
    50: dict(name="servers", subnet="10.10.50",  leaf="leaf2", access=["ethernet-1/50"]),
    60: dict(name="external", subnet="203.0.113", leaf="leaf2", access=["ethernet-1/58"]),
    99: dict(name="mgmt",    subnet="10.10.99",  leaf="leaf1", access=["ethernet-1/57"]),
}

# leaf -> uplink interface facing each spine
UPLINKS = {
    "leaf1": {"spine1": "ethernet-1/1", "spine2": "ethernet-1/2"},
    "leaf2": {"spine1": "ethernet-1/1", "spine2": "ethernet-1/2"},
}
# spine -> downlink interface facing each leaf
DOWNLINKS = {
    "spine1": {"leaf1": "ethernet-1/1", "leaf2": "ethernet-1/2"},
    "spine2": {"leaf1": "ethernet-1/1", "leaf2": "ethernet-1/2"},
}
# spine -> host part of its IRB address in every VLAN. spine1 owns the address
# endpoints use as default gateway; spine2 is a fully configured standby router.
SPINE_ROLE = {"spine1": 1, "spine2": 2}

ENDPOINTS = {
    "user1":     ("10.10.10.10/24", "10.10.10.1"),
    "guest1":    ("10.10.20.10/24", "10.10.20.1"),
    "finance1":  ("10.10.30.10/24", "10.10.30.1"),
    "web1":      ("10.10.40.10/24", "10.10.40.1"),
    "app1":      ("10.10.50.10/24", "10.10.50.1"),
    "external1": ("203.0.113.10/24", "203.0.113.1"),
    "admin1":    ("10.10.99.100/24", "10.10.99.1"),
}

commands = []


def add(target, mode, command):
    commands.append({"target": target, "mode": mode, "command": command})


def srl(target, command):
    add(target, "srl_cli", command)


def bridged_trunk_subinterface(node, interface, vlan_id):
    """One tagged bridged subinterface on a fabric trunk link."""
    srl(node, f"set / interface {interface} subinterface {vlan_id} type bridged")
    srl(node, f"set / interface {interface} subinterface {vlan_id} admin-state enable")
    srl(node, f"set / interface {interface} subinterface {vlan_id} vlan encap single-tagged vlan-id {vlan_id}")


# --- endpoints -------------------------------------------------------------
for host, (address, gateway) in ENDPOINTS.items():
    add(host, "shell", "ip link set eth1 up")
    add(host, "shell", f"ip address replace {address} dev eth1")
    add(host, "shell", f"ip route replace default via {gateway}")

# --- leaves: pure L2 -------------------------------------------------------
for leaf, uplinks in UPLINKS.items():
    leaf_vlans = {vid: spec for vid, spec in VLANS.items() if spec["leaf"] == leaf}

    # Fabric uplinks are 802.1Q trunks carrying only the VLANs local to this leaf.
    for interface in uplinks.values():
        srl(leaf, f"set / interface {interface} admin-state enable")
        srl(leaf, f"set / interface {interface} vlan-tagging true")
        for vlan_id in sorted(leaf_vlans):
            bridged_trunk_subinterface(leaf, interface, vlan_id)

    # Endpoint ports are untagged access ports.
    for vlan_id, spec in sorted(leaf_vlans.items()):
        for interface in spec["access"]:
            srl(leaf, f"set / interface {interface} admin-state enable")
            srl(leaf, f"set / interface {interface} vlan-tagging false")
            srl(leaf, f"set / interface {interface} subinterface 0 type bridged")
            srl(leaf, f"set / interface {interface} subinterface 0 admin-state enable")

    # One bridge domain per VLAN, joining the access ports to both uplinks.
    for vlan_id, spec in sorted(leaf_vlans.items()):
        instance = f"vlan{vlan_id}"
        srl(leaf, f"set / network-instance {instance} type mac-vrf")
        srl(leaf, f"set / network-instance {instance} admin-state enable")
        srl(leaf, f"set / network-instance {instance} description {spec['name']}-vlan{vlan_id}")
        for interface in spec["access"]:
            srl(leaf, f"set / network-instance {instance} interface {interface}.0")
        for interface in uplinks.values():
            srl(leaf, f"set / network-instance {instance} interface {interface}.{vlan_id}")

# --- spines: L3 inter-VLAN gateways ---------------------------------------
for spine, downlinks in DOWNLINKS.items():
    host_part = SPINE_ROLE[spine]

    for interface in downlinks.values():
        srl(spine, f"set / interface {interface} admin-state enable")
        srl(spine, f"set / interface {interface} vlan-tagging true")

    srl(spine, "set / interface irb0 admin-state enable")

    for vlan_id, spec in sorted(VLANS.items()):
        downlink = downlinks[spec["leaf"]]
        instance = f"vlan{vlan_id}"
        subnet = spec["subnet"]
        irb_address = f"{subnet}.{host_part}/24"

        bridged_trunk_subinterface(spine, downlink, vlan_id)

        # Routed anchor for the VLAN in the default ip-vrf.
        srl(spine, f"set / interface irb0 subinterface {vlan_id} admin-state enable")
        srl(spine, f"set / interface irb0 subinterface {vlan_id} description {spec['name']}-gateway")
        srl(spine, f"set / interface irb0 subinterface {vlan_id} ipv4 admin-state enable")
        srl(spine, f"set / interface irb0 subinterface {vlan_id} ipv4 address {irb_address}")

        # The bridge domain terminates on the IRB; the IRB is routed in the default ip-vrf.
        srl(spine, f"set / network-instance {instance} type mac-vrf")
        srl(spine, f"set / network-instance {instance} admin-state enable")
        srl(spine, f"set / network-instance {instance} description {spec['name']}-vlan{vlan_id}")
        srl(spine, f"set / network-instance {instance} interface {downlink}.{vlan_id}")
        srl(spine, f"set / network-instance {instance} interface irb0.{vlan_id}")
        srl(spine, f"set / network-instance default interface irb0.{vlan_id}")

payload = {
    "id": "sme01_vlan_l2_routed_spine_healthy",
    "description": (
        "Healthy reference state for SME01-vlan. Leaves are pure layer-2 switches with one "
        "mac-vrf bridge domain per VLAN, untagged access ports, and 802.1Q trunks to both "
        "spines. Spines are the layer-3 inter-VLAN gateways: each spine holds one IRB "
        "subinterface per VLAN in the default ip-vrf, so every VLAN prefix is directly "
        "connected and no static route is needed. spine1 owns the .1 address used by the "
        "endpoints as default gateway; spine2 holds .2 in every VLAN and is a fully "
        "configured standby inter-VLAN router."
    ),
    "commands": commands,
}

OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
srl_count = sum(1 for c in commands if c["mode"] == "srl_cli")
print(f"{OUT}: {len(commands)} commands ({srl_count} srl_cli, {len(commands)-srl_count} shell)")
