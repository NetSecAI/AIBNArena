#!/usr/bin/env python3
"""Generate the healthy reference state for the sme01-fw lab.

Four layers, each with one job:

  leaves    pure layer-2 access, one mac-vrf per VLAN, untagged access ports
  spine1    layer-2 aggregation, carrying every VLAN up to the firewall
  fw-int    the inter-VLAN gateway and the internal segmentation policy
  fw-ext    the DMZ and the Internet, and the service-level policy in front of them

The two firewalls are back to back: fw-int owns every internal VLAN and decides
what may talk to what inside the enterprise, then hands everything leaving the
estate to fw-ext over a routed transit link. fw-ext alone touches the DMZ and
the OUTSIDE zone. Neither can be bypassed -- there is no path from an internal
endpoint to a DMZ server that does not cross both.

Coarse egress at fw-int, fine-grained service policy at fw-ext: the internal
firewall says which VLAN may leave at all and on which ports, the external one
says which host may reach which server. Stacking them is the point of a
two-firewall DMZ, not an accident of the layout.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
OUT = REPO / "benchmarks/testbeds/containerlab/sme01-fw/states/healthy.json"

# vlan_id -> (name, subnet, leaf holding it, access ports on that leaf, zone)
VLANS = {
    10: dict(name="users",   subnet="10.10.10", leaf="leaf1", access=["ethernet-1/10"], zone="USERS"),
    20: dict(name="guest",   subnet="10.10.20", leaf="leaf1", access=["ethernet-1/20", "ethernet-1/30"], zone="GUEST"),
    30: dict(name="finance", subnet="10.10.30", leaf="leaf2", access=["ethernet-1/30"], zone="FINANCE"),
    99: dict(name="mgmt",    subnet="10.10.99", leaf="leaf1", access=["ethernet-1/57"], zone="MGMT"),
}

# --- the layer-2 fabric ----------------------------------------------------
# One aggregation switch. A second one was built and removed: SR Linux has no
# vPC-style MLAG -- its multi-chassis LAG is an EVPN ethernet-segment, which
# the commit rejects without a bgp-vpn instance -- so a second spine could only
# ever be a configured-but-disabled standby. It carried no traffic, no fault
# could target it, and no connectivity requirement asserted it. The README
# records what it would take to make a second spine earn its place.
LEAF_UPLINKS = {
    "leaf1": {"spine1": "ethernet-1/1"},
    "leaf2": {"spine1": "ethernet-1/1"},
}
SPINE_DOWNLINKS = {
    "spine1": {"leaf1": "ethernet-1/1", "leaf2": "ethernet-1/2"},
}
# The aggregation switch's trunk toward fw-int, and the firewall port facing it.
SPINE_UPLINK = {"spine1": "ethernet-1/3"}
FW_INT_TRUNK = {"spine1": "eth1"}

# --- the transit between the two firewalls ---------------------------------
TRANSIT = dict(subnet="10.255.1", fw_int_port="eth2", fw_int_address="10.255.1.1/30",
               fw_ext_port="eth1", fw_ext_address="10.255.1.2/30")

# --- what hangs off the external firewall ----------------------------------
# port -> (subnet, zone, description). No switch in between: the DMZ servers
# and the outside client are cabled straight to fw-ext.
FW_EXT_PORTS = {
    "eth2": dict(subnet="10.10.40", zone="DMZ", name="dmz-web"),
    "eth3": dict(subnet="10.10.50", zone="DMZ", name="dmz-app"),
    "eth4": dict(subnet="203.0.113", zone="OUTSIDE", name="outside"),
}

WEB, APP = "10.10.40.10", "10.10.50.10"
USER1, GUEST1, FINANCE1, ADMIN1 = "10.10.10.10", "10.10.20.10", "10.10.30.10", "10.10.99.100"
EXTERNAL1 = "203.0.113.10"
WAN_PORT = "eth4"                       # fw-ext's only interface facing OUTSIDE
INTERNAL_ZONES = ["USERS", "GUEST", "FINANCE", "MGMT"]
UPLINK_ZONE = "UPLINK"                  # fw-int's zone facing fw-ext

ANY = None

# --- fw-int: internal segmentation -----------------------------------------
# A rule is (protocol, source, destination, port, description); None means any.
# A zone pair absent from this table has no ruleset and is denied by the zone
# model itself -- which is what keeps guest away from finance without a single
# deny rule being written.
#
# Toward the uplink the rules are deliberately coarse: fw-int decides which
# VLAN may leave the enterprise and on which service ports, and leaves "which
# host may reach which server" to fw-ext. Filtering the same flow twice, in the
# same terms, would make the second firewall decoration.
POLICY_INT = {
    ("USERS", UPLINK_ZONE): [
        ("tcp",  None, None, 80,   "users-http-out"),
        ("tcp",  None, None, 443,  "users-tls-out"),
        ("tcp",  None, None, 5201, "users-app-out"),
        ("icmp", None, None, None, "users-ping-out"),
    ],
    ("GUEST", UPLINK_ZONE): [
        ("tcp",  None, None, 80,   "guest-http-out"),
        ("tcp",  None, None, 443,  "guest-tls-out"),
        ("icmp", None, None, None, "guest-ping-out"),
    ],
    ("FINANCE", UPLINK_ZONE): [
        ("tcp",  None, None, 443,  "finance-tls-out"),
        ("tcp",  None, None, 5201, "finance-app-out"),
        ("icmp", None, None, None, "finance-ping-out"),
    ],
    # The administration station reaches everything, inside and out.
    ("MGMT", UPLINK_ZONE):  [(ANY, None, None, None, "admin-egress")],
    ("MGMT", "USERS"):      [(ANY, None, None, None, "admin-to-users")],
    ("MGMT", "GUEST"):      [(ANY, None, None, None, "admin-to-guest")],
    ("MGMT", "FINANCE"):    [(ANY, None, None, None, "admin-to-finance")],
    # Nothing beyond the uplink opens a session inland; ICMP is allowed only
    # because the benchmark oracle probes every required flow in both
    # directions. A service-aware oracle would let these three go away.
    (UPLINK_ZONE, "USERS"):   [("icmp", None, None, None, "uplink-ping-users")],
    (UPLINK_ZONE, "GUEST"):   [("icmp", None, None, None, "uplink-ping-guest")],
    (UPLINK_ZONE, "FINANCE"): [("icmp", None, None, None, "uplink-ping-finance")],
}
# Return paths that carry no new session, only replies.
REPLY_ONLY_INT = [(zone, "MGMT") for zone in ["USERS", "GUEST", "FINANCE", UPLINK_ZONE]]

# --- fw-ext: the DMZ and the Internet --------------------------------------
# Every internal subnet arrives on the transit link, so the INTERNAL zone here
# is one interface. Per-host granularity comes from the source address, which
# survives intact: fw-int routes, it does not translate.
POLICY_EXT = {
    ("INTERNAL", "DMZ"): [
        ("tcp",  USER1,    WEB, 80,   "user1-to-web"),
        ("icmp", USER1,    WEB, None, "user1-ping-web"),
        ("tcp",  USER1,    APP, 5201, "user1-to-app"),
        ("icmp", USER1,    APP, None, "user1-ping-app"),
        ("tcp",  GUEST1,   WEB, 80,   "guest1-to-web"),
        ("icmp", GUEST1,   WEB, None, "guest1-ping-web"),
        ("tcp",  FINANCE1, APP, 5201, "finance1-to-app"),
        ("icmp", FINANCE1, APP, None, "finance1-ping-app"),
        (ANY,    ADMIN1,   None, None, "admin-to-dmz"),
    ],
    ("INTERNAL", "OUTSIDE"): [
        ("tcp",  None,   None, 80,   "internal-http-out"),
        ("tcp",  None,   None, 443,  "internal-tls-out"),
        ("icmp", None,   None, None, "internal-ping-out"),
        (ANY,    ADMIN1, None, None, "admin-to-outside"),
    ],
    ("OUTSIDE", "DMZ"): [
        ("tcp",  EXTERNAL1, WEB, 80,   "internet-to-web"),
        ("icmp", EXTERNAL1, WEB, None, "internet-ping-web"),
    ],
    # A DMZ server never initiates a session inland. ICMP only, and only toward
    # the endpoints the oracle probes from.
    ("DMZ", "INTERNAL"): [
        ("icmp", WEB, USER1,    None, "web1-ping-user1"),
        ("icmp", APP, USER1,    None, "app1-ping-user1"),
        ("icmp", WEB, GUEST1,   None, "web1-ping-guest1"),
        ("icmp", APP, FINANCE1, None, "app1-ping-finance1"),
    ],
    ("DMZ", "OUTSIDE"): [("icmp", WEB, EXTERNAL1, None, "web1-ping-outside")],
}
# The deny that matters most: nothing on the Internet opens a session toward
# the enterprise.
REPLY_ONLY_EXT = [("OUTSIDE", "INTERNAL")]

ENDPOINTS = {
    "user1":     ("10.10.10.10/24", "10.10.10.1"),
    "guest1":    ("10.10.20.10/24", "10.10.20.1"),
    "finance1":  ("10.10.30.10/24", "10.10.30.1"),
    "admin1":    ("10.10.99.100/24", "10.10.99.1"),
    "web1":      ("10.10.40.10/24", "10.10.40.1"),
    "app1":      ("10.10.50.10/24", "10.10.50.1"),
    "external1": ("203.0.113.10/24", "203.0.113.1"),
}

commands = []


def add(target, mode, command):
    commands.append({"target": target, "mode": mode, "command": command})


def srl(target, command):
    add(target, "srl_cli", command)


def vyos(target, command):
    add(target, "vyos_cli", command)


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

# --- leaves: pure layer-2 --------------------------------------------------
for leaf, uplinks in LEAF_UPLINKS.items():
    leaf_vlans = {vid: spec for vid, spec in VLANS.items() if spec["leaf"] == leaf}

    # Both uplinks are 802.1Q trunks carrying the VLANs local to this leaf.
    for interface in uplinks.values():
        srl(leaf, f"set / interface {interface} admin-state enable")
        srl(leaf, f"set / interface {interface} vlan-tagging true")
        for vlan_id in sorted(leaf_vlans):
            bridged_trunk_subinterface(leaf, interface, vlan_id)

    for vlan_id, spec in sorted(leaf_vlans.items()):
        for interface in spec["access"]:
            srl(leaf, f"set / interface {interface} admin-state enable")
            srl(leaf, f"set / interface {interface} vlan-tagging false")
            srl(leaf, f"set / interface {interface} subinterface 0 type bridged")
            srl(leaf, f"set / interface {interface} subinterface 0 admin-state enable")

    for vlan_id, spec in sorted(leaf_vlans.items()):
        instance = f"vlan{vlan_id}"
        srl(leaf, f"set / network-instance {instance} type mac-vrf")
        srl(leaf, f"set / network-instance {instance} admin-state enable")
        srl(leaf, f"set / network-instance {instance} description {spec['name']}-vlan{vlan_id}")
        for interface in spec["access"]:
            srl(leaf, f"set / network-instance {instance} interface {interface}.0")
        for interface in uplinks.values():
            srl(leaf, f"set / network-instance {instance} interface {interface}.{vlan_id}")

# --- spines: pure layer-2 aggregation --------------------------------------
# Both spines are configured identically and carry every VLAN from both leaves.
# Neither holds an IP address: the gateway moved to fw-int, and a spine that
# routed would be a way around it.
for spine, downlinks in SPINE_DOWNLINKS.items():
    uplink = SPINE_UPLINK[spine]
    trunks = list(downlinks.values()) + [uplink]

    for interface in trunks:
        srl(spine, f"set / interface {interface} admin-state enable")
        srl(spine, f"set / interface {interface} vlan-tagging true")

    for vlan_id, spec in sorted(VLANS.items()):
        downlink = downlinks[spec["leaf"]]
        instance = f"vlan{vlan_id}"
        bridged_trunk_subinterface(spine, downlink, vlan_id)
        bridged_trunk_subinterface(spine, uplink, vlan_id)

        # One bridge domain per VLAN, joining the leaf that owns it to the
        # firewall trunk. A VLAN lives on exactly one leaf, so a frame reaching
        # a spine is never bridged back toward the other leaf: no loop, and
        # therefore no spanning tree.
        srl(spine, f"set / network-instance {instance} type mac-vrf")
        srl(spine, f"set / network-instance {instance} admin-state enable")
        srl(spine, f"set / network-instance {instance} description {spec['name']}-vlan{vlan_id}")
        srl(spine, f"set / network-instance {instance} interface {downlink}.{vlan_id}")
        srl(spine, f"set / network-instance {instance} interface {uplink}.{vlan_id}")

# --- fw-int: one gateway interface per VLAN --------------------------------
zone_interfaces_int = {zone: [] for zone in INTERNAL_ZONES + [UPLINK_ZONE]}

for vlan_id, spec in sorted(VLANS.items()):
    for spine, port in FW_INT_TRUNK.items():
        vif = f"set interfaces ethernet {port} vif {vlan_id}"
        vyos("fw-int", f"{vif} address {spec['subnet']}.1/24")
        vyos("fw-int", f"{vif} description {spec['name']}-gateway")
        zone_interfaces_int[spec["zone"]].append(f"{port}.{vlan_id}")

vyos("fw-int", f"set interfaces ethernet {TRANSIT['fw_int_port']} address {TRANSIT['fw_int_address']}")
vyos("fw-int", f"set interfaces ethernet {TRANSIT['fw_int_port']} description transit-to-fw-ext")
zone_interfaces_int[UPLINK_ZONE].append(TRANSIT["fw_int_port"])

# Everything that is not an internal VLAN is behind the external firewall.
# Explicit prefixes, not a default route: containerlab installs its own
# management default via eth0 as a kernel route, and a static 0.0.0.0/0 loses
# to it in the FIB -- the route is configured, shows up in `show ip route`
# without the `>*` selected marker, and forwards nothing. Every internal
# endpoint was unreachable on the first run for exactly this reason.
for _spec in sorted(FW_EXT_PORTS.values(), key=lambda item: item["subnet"]):
    vyos("fw-int", f"set protocols static route {_spec['subnet']}.0/24 "
                   f"next-hop {TRANSIT['fw_ext_address'].split('/')[0]}")

# --- fw-ext: transit, DMZ and OUTSIDE --------------------------------------
zone_interfaces_ext = {"INTERNAL": [], "DMZ": [], "OUTSIDE": []}

vyos("fw-ext", f"set interfaces ethernet {TRANSIT['fw_ext_port']} address {TRANSIT['fw_ext_address']}")
vyos("fw-ext", f"set interfaces ethernet {TRANSIT['fw_ext_port']} description transit-to-fw-int")
zone_interfaces_ext["INTERNAL"].append(TRANSIT["fw_ext_port"])

for port, spec in sorted(FW_EXT_PORTS.items()):
    vyos("fw-ext", f"set interfaces ethernet {port} address {spec['subnet']}.1/24")
    vyos("fw-ext", f"set interfaces ethernet {port} description {spec['name']}-gateway")
    zone_interfaces_ext[spec["zone"]].append(port)

# Every internal subnet is reached back through the internal firewall.
for vlan_id, spec in sorted(VLANS.items()):
    vyos("fw-ext", f"set protocols static route {spec['subnet']}.0/24 "
                   f"next-hop {TRANSIT['fw_int_address'].split('/')[0]}")

# --- zones -----------------------------------------------------------------
for target, zone_interfaces in (("fw-int", zone_interfaces_int), ("fw-ext", zone_interfaces_ext)):
    for zone, interfaces in zone_interfaces.items():
        vyos(target, f"set firewall zone {zone} description {zone.lower()}-zone")
        for interface in interfaces:
            vyos(target, f"set firewall zone {zone} interface {interface}")


# --- least-privilege policy on both firewalls ------------------------------
def emit_ruleset(target, from_zone, to_zone, rules):
    name = f"{from_zone}-TO-{to_zone}"
    vyos(target, f"set firewall ipv4 name {name} default-action drop")
    vyos(target, f"set firewall ipv4 name {name} description {from_zone.lower()}-to-{to_zone.lower()}")
    # Replies to sessions this firewall already allowed always come back.
    vyos(target, f"set firewall ipv4 name {name} rule 10 action accept")
    vyos(target, f"set firewall ipv4 name {name} rule 10 description return-traffic")
    vyos(target, f"set firewall ipv4 name {name} rule 10 state established")
    vyos(target, f"set firewall ipv4 name {name} rule 10 state related")
    for index, (protocol, source, destination, port, description) in enumerate(rules, start=1):
        rule = 10 * (index + 1)
        prefix = f"set firewall ipv4 name {name} rule {rule}"
        vyos(target, f"{prefix} action accept")
        vyos(target, f"{prefix} description {description}")
        if protocol:
            vyos(target, f"{prefix} protocol {protocol}")
        if protocol == "icmp":
            vyos(target, f"{prefix} icmp type-name echo-request")
        if source:
            vyos(target, f"{prefix} source address {source}")
        if destination:
            vyos(target, f"{prefix} destination address {destination}")
        if port:
            vyos(target, f"{prefix} destination port {port}")
    vyos(target, f"set firewall zone {to_zone} from {from_zone} firewall name {name}")


for target, policy, reply_only in (("fw-int", POLICY_INT, REPLY_ONLY_INT),
                                   ("fw-ext", POLICY_EXT, REPLY_ONLY_EXT)):
    for (from_zone, to_zone), rules in sorted(policy.items()):
        emit_ruleset(target, from_zone, to_zone, rules)
    for from_zone, to_zone in sorted(reply_only):
        emit_ruleset(target, from_zone, to_zone, [])

# --- NAT, on the external firewall only ------------------------------------
# fw-int routes and does not translate, which is what keeps the real internal
# source address visible to fw-ext's per-host rules. Translation happens once,
# on the way to the Internet.
for index, (vlan_id, spec) in enumerate(sorted(VLANS.items()), start=1):
    rule = 100 + index
    vyos("fw-ext", f"set nat source rule {rule} description {spec['name']}-masquerade")
    vyos("fw-ext", f"set nat source rule {rule} outbound-interface name {WAN_PORT}")
    vyos("fw-ext", f"set nat source rule {rule} source address {spec['subnet']}.0/24")
    vyos("fw-ext", f"set nat source rule {rule} translation address masquerade")

vyos("fw-ext", "set nat destination rule 10 description publish-web")
vyos("fw-ext", f"set nat destination rule 10 inbound-interface name {WAN_PORT}")
vyos("fw-ext", "set nat destination rule 10 protocol tcp")
vyos("fw-ext", "set nat destination rule 10 destination port 80")
vyos("fw-ext", f"set nat destination rule 10 translation address {WEB}")

payload = {
    "id": "sme01_fw_dual_firewall_healthy",
    "description": (
        "Healthy reference state for SME01-fw. Layer-2 leaves trunk to a single layer-2 "
        "aggregation switch, which trunks every VLAN to the internal firewall. fw-int is "
        "the inter-VLAN gateway and the internal segmentation policy -- one zone per VLAN, so "
        "a zone pair with no ruleset is denied by the zone model -- and it routes everything "
        "leaving the enterprise over a transit link to fw-ext. fw-ext owns the DMZ and the "
        "OUTSIDE zone, applies the service-level policy on source address and port, "
        "masquerades internal traffic toward the Internet and publishes the DMZ web service. "
        "Coarse egress at the internal firewall, fine-grained service policy at the external "
        "one; ICMP echo is opened on every declared flow because the benchmark oracle probes "
        "reachability with ping."
    ),
    "commands": commands,
}

OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
counts = {}
for c in commands:
    counts[c["mode"]] = counts.get(c["mode"], 0) + 1
print(f"{OUT}: {len(commands)} commands {counts}")
