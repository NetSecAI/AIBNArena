#!/usr/bin/env python3
"""Generate the healthy reference state for the campus_small lab.

campus_small is campus_medium with its fabric folded down: two layer-2 Arista
access switches cabled straight to a single VyOS firewall -- no aggregates, so no
MLAG, each trunk a plain port, rendered from the descriptor by
benchmarks/testbeds/campus_fabric.py -- the firewall holding what medium's
two firewalls held between them -- the gateway of every LAN VLAN, the
intra-LAN policy, the DMZ, the Internet, the NAT, the desks' DHCP service and the LAN
shaper. The endpoints, the two resolvers, the VLAN and address plan, and every
ruleset are medium's (and so the original lab's). What a single box
changes:

  transit   gone, and with it firewall-interne's TRANSIT zone, its TRUST-TRANSIT
            and TRANSIT-LOCAL rulesets, and the 10.255.255.0/30 link. LAN egress
            meets TRUST-DMZ and TRUST-UNTRUST on the box that routes it.
  groups    both of medium's firewalls had an address-group SRV-DNS, each its
            own resolver. Here they share one box: the LAN resolver keeps
            SRV-DNS (TRUST-INTRA), the DMZ one becomes SRV-DNS-DMZ (TRUST-DMZ).
  LOCAL     TRUST-LOCAL is firewall-interne's (DHCP and ping): this box serves
            the desks' leases, which the DMZ firewall's ping-only one did not.
  QoS       one shaper per access trunk: QOS-UPLINK-ACC1 on eth1 (CORP, VOIP),
            QOS-UPLINK-ACC2 on eth2 (BMS, GUESTS), the original classes unchanged.
  DMZ       DMZ-INTRA filters inside the DMZ zone (default drop, the DMZ
            resolver and ping allowed), as medium's does since 2026-10-28.
  LLDP      on the two trunks, eth1 and eth2, as medium's firewall-interne on
            bond0's members: the original's firewalls ran none, and without it
            neither trunk could be read from LLDP.

Not the original's: the remote site on eth7 (benchmarks/testbeds/campus_site.py),
which gives the lab a routed hop and a shaped circuit. Two states come out of
this script, because the QoS families disagree on one thing -- whether wan-edge
already carries its class policy:

    healthy.json             policy present   -> wan_shaping_policy_repair, the rest
    healthy_greenfield.json  policy absent    -> assured_bandwidth

Routes, the shaping policy and the iperf3 sinks are read from the descriptor; the
tables below hold the rest. Edit them, not the JSON.
"""
import json
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))

from benchmarks.testbeds import campus_fabric, campus_site  # noqa: E402
from scenarios.compiler.loader import load_yaml  # noqa: E402

OUT_DIR = REPO / "benchmarks/testbeds/containerlab/campus_small/states"
TOPOLOGY = load_yaml(REPO / "scenarios/topologies/campus_small.yaml")["topology"]

FW = "firewall"

# --- the LAN VLANs ----------------------------------------------------------
# vlan_id -> group, subnet, the firewall trunk the VLAN arrives on, and the host
# it serves. Where it lives on the switches is the descriptor's. A VLAN lives on
# exactly one access switch.
#
# VLAN 130 is the infrastructure VLAN: it holds dns-access-4 alone, at a static
# address, so it serves no DHCP. Keeping the resolver out of a user VLAN is what
# lets TRUST-INTRA isolate the groups without punching a DNS hole into one.
VLANS = {
    100: dict(group="CORP",   subnet="10.0.0", trunk="eth1", host="host-access-1"),
    110: dict(group="VOIP",   subnet="10.0.1", trunk="eth1", host="host-access-2"),
    120: dict(group="BMS",    subnet="10.0.2", trunk="eth2", host="host-access-3"),
    130: dict(group="INFRA",  subnet="10.0.3", trunk="eth2", host="dns-access-4"),
    140: dict(group="GUESTS", subnet="10.0.4", trunk="eth2", host="host-access-4"),
}
# firewall trunk -> the access switch at its other end.
FW_TRUNKS = {"eth1": "switch-access-1", "eth2": "switch-access-2"}

# --- what hangs off the firewall besides the LAN -------------------------------
# port -> address, zone, description. The servers are cabled straight to it:
# every packet between the LAN and a DMZ service crosses the zone policy.
FW_PORTS = {
    "eth3": ("172.16.2.254/24", "DMZ", "DMZ web-server segment"),
    "eth4": ("172.16.1.254/24", "DMZ", "DMZ SFTP-server segment"),
    "eth5": ("172.16.3.254/24", "DMZ", "DMZ DNS-server segment"),
    "eth6": ("192.168.122.202/24", "UNTRUST", "Untrust / Internet segment"),
    "eth7": ("10.255.254.0/31", "SITE", "WAN to the remote site, through router-wan"),
}
WAN_PORT = "eth6"

LAN_RESOLVER, DMZ_RESOLVER = "10.0.3.53", "172.16.3.53"

# --- DHCP -------------------------------------------------------------------
# LAN desk -> pinned MAC and reserved address, as in campus_medium. The MAC spells
# the address (0a.00.0X.96 = 10.0.X.150) and is pinned in topology.clab.yml.
LAN_DHCP_CLIENTS = {
    "host-access-1": ("02:00:0a:00:00:96", "10.0.0.150"),
    "host-access-2": ("02:00:0a:00:01:96", "10.0.1.150"),
    "host-access-3": ("02:00:0a:00:02:96", "10.0.2.150"),
    "host-access-4": ("02:00:0a:00:04:96", "10.0.4.150"),
}
# A reservation pool rebuilt by a DHCP fault's restore carries exactly these
# fields (benchmarks/platforms/containerlab/compiled_topology.py).
LAN_DOMAIN_NAME = "campus.lab"
LAN_LEASE_SECONDS = 86400
# dhcp/udhcpc.script says why the stock BusyBox hook cannot be used.
UDHCPC_SCRIPT = "/opt/dhcp/udhcpc.script"
UDHCPC_PIDFILE = "/var/run/udhcpc.eth1.pid"

# Statically addressed nodes: address, gateway.
STATIC_HOSTS = {
    "dns-access-4": ("10.0.3.53/24", "10.0.3.1"),
    "dns-dmz":      ("172.16.3.53/24", "172.16.3.254"),
    "external1":    ("192.168.122.1/24", "192.168.122.202"),
    "sftp-dmz":     ("172.16.1.10/24", "172.16.1.254"),
    "web-dmz":      ("172.16.2.10/24", "172.16.2.254"),
}

# Static since 2026-11-06: only the desks lease. The original's DMZ servers leased
# from a static-mapping, but a renewal rewrites a client's address, link and default
# route, and the judge and the ANI both renew, so a fault on those was repaired by
# the bench (the intent translation report of main01). What a lease carried beyond
# the address and route, their resolver and domain, is set below.
DMZ_SERVERS = ("sftp-dmz", "web-dmz")
DMZ_DOMAIN_NAME = "dmz.lab"

# Mounted read-only under /opt/dns-src, copied into /etc/bind, so the running
# config stays writable for a fault and the restore is a copy back.
DNS_FILES = {
    "dns-access-4": ["named.conf.options", "named.conf.local", "db.campus.lab"],
    "dns-dmz": ["named.conf.options", "named.conf.local", "db.dmz.lab"],
}

# --- QoS on the access trunks ----------------------------------------------------
# class -> description, matched vif, bandwidth, priority, ceiling: the original
# QOS-UPLINK's classes. Each trunk gets the classes of its own vifs -- a class
# matching a vif its port does not hold is a QoS rule on an L3 interface that is
# not there.
QOS_POLICIES = {"eth1": "QOS-UPLINK-ACC1", "eth2": "QOS-UPLINK-ACC2"}
QOS_CLASSES = {
    100: ("CORP-USERS",  "CORP",   "50%", 2, None),
    110: ("VOIP",        "VOIP",   "15%", 1, "100%"),
    120: ("BMS-DEVICES", "BMS",    "10%", 4, None),
    140: ("GUESTS",      "GUESTS", "25%", 5, None),
}

# --- firewall policy --------------------------------------------------------
# A rule is (number, description, protocol, source, destination, port); in
# source and destination a ("address-group"|"network-group", name) pair is a
# group match, a plain string an address. The rulesets are campus_medium's, rule
# for rule, minus the two that existed only for the transit link.
ANY = None
ADDRESS, NETWORK = "address-group", "network-group"

GROUPS = {
    (ADDRESS, "SRV-DNS"): [LAN_RESOLVER],
    (ADDRESS, "SRV-DNS-DMZ"): [DMZ_RESOLVER],
    (ADDRESS, "SRV-FTP"): ["172.16.1.10"],
    (NETWORK, "BMS"): ["10.0.2.0/24"],
    (NETWORK, "CORP"): ["10.0.0.0/24"],
    (NETWORK, "GUESTS"): ["10.0.4.0/24"],
    (NETWORK, "VOIP"): ["10.0.1.0/24"],
}

INTERNET_RULES = [
    (10, "ALLOW-{}-junos-http", "tcp", ANY, ANY, 80),
    (20, "ALLOW-{}-junos-https", "tcp", ANY, ANY, 443),
    (30, "ALLOW-{}-junos-http-ext", "tcp", ANY, ANY, 8080),
    (40, "ALLOW-UTILS-junos-ping", "icmp", ANY, ANY, ANY),
    (50, "ALLOW-UTILS-junos-dns-tcp", "tcp", ANY, ANY, 53),
    (60, "ALLOW-UTILS-junos-dns-udp", "udp", ANY, ANY, 53),
]
# name -> (default action, description, rules)
RULESETS = {
    # The three DMZ segments are one zone, so their traffic is intra-zone, which
    # VyOS permits by default. Not the original's: the HLD's DMZ-to-DMZ default
    # deny, with the DMZ resolver the only service reachable across segments.
    "DMZ-INTRA": ("drop", None, [
        (10, "ALLOW-DNS-dmz-udp dst=SRV-DNS-DMZ", "udp", ANY, (ADDRESS, "SRV-DNS-DMZ"), 53),
        (15, "ALLOW-DNS-dmz-tcp dst=SRV-DNS-DMZ", "tcp", ANY, (ADDRESS, "SRV-DNS-DMZ"), 53),
        (20, "ALLOW-UTILS-junos-ping", "icmp", ANY, ANY, ANY),
    ]),
    "DMZ-LOCAL": ("drop", None, [
        (20, "ALLOW-GATEWAY-ping", "icmp", ANY, ANY, ANY),
    ]),
    "DMZ-UNTRUST": ("drop", None, [
        (n, d.format("HTTP"), p, s, t, port) for n, d, p, s, t, port in INTERNET_RULES
    ]),
    "LOCAL-OUT": ("accept", "Router own outbound traffic (unfiltered, as before the local zone)", []),
    "MGMT-LOCAL": ("drop", None, [
        (10, "ALLOW-MGMT-ssh", "tcp", ANY, ANY, 22),
        (20, "ALLOW-MGMT-https-api", "tcp", ANY, ANY, 443),
        (30, "ALLOW-MGMT-ping", "icmp", ANY, ANY, ANY),
    ]),
    "TRUST-DMZ": ("drop", None, [
        (10, "ACCES-FTP-junos-ftp dst=SRV-FTP", "tcp", ANY, (ADDRESS, "SRV-FTP"), 21),
        (15, "ACCES-SFTP-ssh dst=SRV-FTP", "tcp", ANY, (ADDRESS, "SRV-FTP"), 22),
        (20, "ACCES-FTP-junos-ping dst=SRV-FTP", "icmp", ANY, (ADDRESS, "SRV-FTP"), ANY),
        (30, "ALLOW-NFS-APP-NFSv4", "tcp", ANY, ANY, 2049),
        (40, "ALLOW-PING-junos-ping", "icmp", ANY, ANY, ANY),
        (50, "ALLOW-WEB-junos-http", "tcp", ANY, ANY, 80),
        # The conditional forward of dmz.lab from dns-access-4 to dns-dmz.
        (60, "ACCES-DNS-udp dst=SRV-DNS-DMZ", "udp", ANY, (ADDRESS, "SRV-DNS-DMZ"), 53),
        (65, "ACCES-DNS-tcp dst=SRV-DNS-DMZ", "tcp", ANY, (ADDRESS, "SRV-DNS-DMZ"), 53),
    ]),
    # The five VLANs are groups inside one zone, so their traffic is intra-zone,
    # which VyOS permits by default: isolating them takes intra-zone-filtering.
    # GUESTS, VOIP and BMS initiate nothing beyond DNS and ICMP.
    "TRUST-INTRA": ("drop", None, [
        (10, "ALLOW-CORP-to-BMS", ANY, (NETWORK, "CORP"), (NETWORK, "BMS"), ANY),
        (20, "ALLOW-CORP-to-VOIP", ANY, (NETWORK, "CORP"), (NETWORK, "VOIP"), ANY),
        (30, "ALLOW-DNS-infra-udp dst=SRV-DNS", "udp", ANY, (ADDRESS, "SRV-DNS"), 53),
        (35, "ALLOW-DNS-infra-tcp dst=SRV-DNS", "tcp", ANY, (ADDRESS, "SRV-DNS"), 53),
        (40, "ALLOW-UTILS-junos-ping", "icmp", ANY, ANY, ANY),
    ]),
    "TRUST-LOCAL": ("drop", None, [
        (10, "ALLOW-DHCP-server", "udp", ANY, ANY, 67),
        (20, "ALLOW-GATEWAY-ping", "icmp", ANY, ANY, ANY),
    ]),
    "TRUST-UNTRUST": ("drop", None, [
        (n, d.format("INTERNET"), p, s, t, port) for n, d, p, s, t, port in INTERNET_RULES
    ]),
    # The remote site. CORP and BMS reach it -- they are the two flows the
    # shaped circuit carries, measured over TCP and loaded over UDP -- and every
    # group may ping it. Nothing at the site opens a session toward the LAN.
    "TRUST-SITE": ("drop", None, [
        (10, "ALLOW-CORP-to-SITE", ANY, (NETWORK, "CORP"), ANY, ANY),
        (20, "ALLOW-BMS-to-SITE", ANY, (NETWORK, "BMS"), ANY, ANY),
        (40, "ALLOW-UTILS-junos-ping", "icmp", ANY, ANY, ANY),
    ]),
    "SITE-LOCAL": ("drop", None, [
        (10, "ALLOW-SITE-ping", "icmp", ANY, ANY, ANY),
    ]),
}

# zone -> interfaces, (from zone -> ruleset), intra-zone ruleset. A zone pair
# absent here has no ruleset and the zone model denies it: nothing on the
# Internet or in the DMZ opens a session toward the LAN.
ZONES = {
    "DMZ": ([p for p, (_a, zone, _d) in sorted(FW_PORTS.items()) if zone == "DMZ"],
            {"LOCAL": "LOCAL-OUT", "TRUST": "TRUST-DMZ"}, "DMZ-INTRA"),
    "LOCAL":([], {"DMZ": "DMZ-LOCAL", "MGMT": "MGMT-LOCAL", "SITE": "SITE-LOCAL",
                   "TRUST": "TRUST-LOCAL"}, None),
    "MGMT": (["eth0"], {"LOCAL": "LOCAL-OUT"}, None),
    "SITE": ([p for p, (_a, zone, _d) in sorted(FW_PORTS.items()) if zone == "SITE"],
             {"LOCAL": "LOCAL-OUT", "TRUST": "TRUST-SITE"}, None),
    "TRUST": ([f"{spec['trunk']}.{vlan_id}" for vlan_id, spec in sorted(VLANS.items())],
              {"LOCAL": "LOCAL-OUT"}, "TRUST-INTRA"),
    "UNTRUST": ([WAN_PORT], {"DMZ": "DMZ-UNTRUST", "LOCAL": "LOCAL-OUT", "TRUST": "TRUST-UNTRUST"}, None),
}

commands = []


def add(target, mode, command):
    commands.append({"target": target, "mode": mode, "command": command})


def vyos(command):
    add(FW, "vyos_cli", command)


def match(side, value):
    """The `source`/`destination` part of a rule, as VyOS words."""
    if isinstance(value, tuple):
        kind, name = value
        return f"{side} group {kind} {name}"
    return f"{side} address {value}"


def ruleset(name, default_action, description, rules):
    prefix = f"set firewall ipv4 name {name}"
    vyos(f"{prefix} default-action {default_action}")
    if description:
        vyos(f"{prefix} description {shlex.quote(description)}")
    for number, rule_description, protocol, source, destination, port in rules:
        rule = f"{prefix} rule {number}"
        vyos(f"{rule} action accept")
        vyos(f"{rule} description {shlex.quote(rule_description)}")
        if protocol:
            vyos(f"{rule} protocol {protocol}")
        if source:
            vyos(f"{rule} {match('source', source)}")
        if destination:
            vyos(f"{rule} {match('destination', destination)}")
        if port:
            vyos(f"{rule} destination port {port}")


# --- endpoints ---------------------------------------------------------------
# apply_state() runs every shell command before the first switch or firewall
# command (benchmarks/platforms/containerlab/env.py), so a one-shot DHCP client
# would always lose the race against the server it needs. udhcpc -b forks into
# the background and keeps retrying until the fabric and the firewall are up.
for host in sorted(LAN_DHCP_CLIENTS):
    add(host, "shell", "ip link set eth1 up")
    add(host, "shell", f"ip link set eth1 mtu {campus_site.MTU}")
    # Kill a client left over from an earlier apply before starting one, so
    # re-applying the state does not stack daemons on the interface.
    add(host, "shell",
        f"sh -c 'kill $(cat {UDHCPC_PIDFILE} 2>/dev/null) 2>/dev/null; "
        f"udhcpc -i eth1 -b -p {UDHCPC_PIDFILE} -s {UDHCPC_SCRIPT} "
        f"-t 4 -T 2 -A 3 -x hostname:{host}'")

# network-multitool ships nginx but its image starts bash -- guarded, so a
# second apply does not fail on a port already bound.
add("web-dmz", "shell", "sh -c 'pidof nginx >/dev/null || nginx'")

for host, (address, gateway) in sorted(STATIC_HOSTS.items()):
    add(host, "shell", "ip link set eth1 up")
    add(host, "shell", f"ip link set eth1 mtu {campus_site.MTU}")
    add(host, "shell", f"ip address replace {address} dev eth1")
    add(host, "shell", f"ip route replace default via {gateway}")

# Written in place: /etc/resolv.conf is a bind mount in a container, so a rename
# over it fails (dhcp/udhcpc.script). The search line names the domain these nodes
# resolve in.
for host in DMZ_SERVERS:
    add(host, "shell",
        f"sh -c 'echo search {DMZ_DOMAIN_NAME} > /etc/resolv.conf; "
        f"echo nameserver {DMZ_RESOLVER} >> /etc/resolv.conf'")

# external1 is where the Internet ends, not a router: forwarding, with its
# default route pointing back at the firewall, it bounced every packet for
# another public address until the TTL ran out.
add("external1", "shell", "sysctl -w net.ipv4.ip_forward=0")

for server, files in sorted(DNS_FILES.items()):
    for name in files:
        add(server, "shell", f"cp /opt/dns-src/{name} /etc/bind/{name}")
    add(server, "shell", "rndc reload")

# --- access switches: pure layer 2 --------------------------------------------
for switch in campus_fabric.switches(TOPOLOGY):
    commands.extend(campus_fabric.fabric_commands(TOPOLOGY, switch))

# --- the firewall: interfaces ----------------------------------------------------
# The original declares containerlab's management port, as the MGMT zone's only
# member; containerlab's own VyOS config does not, and a port absent from the
# config is one a reader of the configuration cannot tell is management. Declaring
# it makes VyOS
# give it an IPv6 link-local address, which the commit fails on: the management
# network is IPv4-only and the kernel has IPv6 off on eth0.
vyos("set interfaces ethernet eth0 description 'Management Interface'")
vyos("set interfaces ethernet eth0 ipv6 address no-default-link-local")
for port, switch in sorted(FW_TRUNKS.items()):
    carried = ",".join(str(v) for v, spec in sorted(VLANS.items()) if spec["trunk"] == port)
    vyos(f"set interfaces ethernet {port} description {shlex.quote(f'Trunk to {switch}, VLANs {carried}')}")
# Not medium's original: LLDP on the two trunks only, so each access switch names
# the firewall on its Ethernet1, and both trunks can be read from LLDP.
for port in sorted(FW_TRUNKS):
    vyos(f"set service lldp interface {port}")
for vlan_id, spec in sorted(VLANS.items()):
    vif = f"set interfaces ethernet {spec['trunk']} vif {vlan_id}"
    vyos(f"{vif} address {spec['subnet']}.1/24")
    vyos(f"{vif} description {shlex.quote(spec['group'] + ' gateway VLAN_' + str(vlan_id))}")
for port, (address, _zone, description) in sorted(FW_PORTS.items()):
    vyos(f"set interfaces ethernet {port} address {address}")
    vyos(f"set interfaces ethernet {port} description {shlex.quote(description)}")

# --- the firewall: routing ----------------------------------------------------------
# As the descriptor declares them: the default toward external1 and the remote site
# through router-wan. No discard route for 10.0.0.0/16 since 2026-10-28: with one
# firewall it guarded no loop, and the agents took it for the fault.
commands.extend(campus_site.static_route_commands(TOPOLOGY, FW))

# --- the firewall: QoS ---------------------------------------------------------------
for port, policy in sorted(QOS_POLICIES.items()):
    shaper = f"set qos policy shaper {policy}"
    vyos(f"{shaper} bandwidth 1gbit")
    vyos(f"{shaper} description {shlex.quote(f'CoS {port} - deterministic VyOS shaper')}")
    for vlan_id, (description, match_name, bandwidth, priority, ceiling) in sorted(QOS_CLASSES.items()):
        if VLANS[vlan_id]["trunk"] != port:
            continue
        vyos(f"{shaper} class {vlan_id} bandwidth {bandwidth}")
        vyos(f"{shaper} class {vlan_id} description {description}")
        vyos(f"{shaper} class {vlan_id} match {match_name} vif {vlan_id}")
        vyos(f"{shaper} class {vlan_id} priority {priority}")
        if ceiling:
            vyos(f"{shaper} class {vlan_id} ceiling {ceiling}")
    vyos(f"{shaper} default bandwidth 1%")
    vyos(f"{shaper} default priority 6")
    vyos(f"set qos interface {port} egress {policy}")

# --- the firewall: zones and policy ----------------------------------------------------
# Replies to sessions a ruleset allowed always come back, whatever the pair:
# this is what lets every ruleset enumerate new sessions only.
vyos("set firewall global-options state-policy established action accept")
vyos("set firewall global-options state-policy related action accept")
for (kind, name), members in sorted(GROUPS.items()):
    word = "address" if kind == ADDRESS else "network"
    for member in members:
        vyos(f"set firewall group {kind} {name} {word} {member}")
for name, (default_action, description, rules) in sorted(RULESETS.items()):
    ruleset(name, default_action, description, rules)
for zone, (interfaces, sources, intra) in sorted(ZONES.items()):
    vyos(f"set firewall zone {zone} default-action drop")
    if zone == "LOCAL":
        vyos(f"set firewall zone {zone} local-zone")
    for interface in interfaces:
        vyos(f"set firewall zone {zone} interface {interface}")
    for source, name in sorted(sources.items()):
        vyos(f"set firewall zone {zone} from {source} firewall name {name}")
    if intra:
        vyos(f"set firewall zone {zone} intra-zone-filtering firewall name {intra}")
# The connection-tracking helpers the original firewalls load, for the FTP and
# NFS rules.
for module in ("ftp", "nfs", "sip", "tftp"):
    vyos(f"set system conntrack modules {module}")

# --- the firewall: NAT ------------------------------------------------------------------
for rule, description, source in (
    (100, "NAT MASQ trust->untrust", "10.0.0.0/8"),
    (200, "NAT NAT-INTERNET DMZ->untrust", "172.16.0.0/16"),
):
    vyos(f"set nat source rule {rule} description {shlex.quote(description)}")
    vyos(f"set nat source rule {rule} outbound-interface name {WAN_PORT}")
    vyos(f"set nat source rule {rule} source address {source}")
    vyos(f"set nat source rule {rule} translation address masquerade")

# --- the firewall: DHCP -----------------------------------------------------------------
# Each LAN VLAN is its own shared-network: grouped under one, kea picks the
# shared-network by incoming interface but may then allocate from any subnet in
# it, stranding a desk on a VLAN whose gateway it cannot reach.
for host, (mac, address) in sorted(LAN_DHCP_CLIENTS.items()):
    vlan_id = next(v for v, spec in VLANS.items() if spec["host"] == host)
    subnet_prefix = VLANS[vlan_id]["subnet"]
    subnet = (f"set service dhcp-server shared-network-name VLAN_{vlan_id} "
              f"subnet {subnet_prefix}.0/24")
    vyos(f"{subnet} subnet-id {vlan_id}")
    vyos(f"{subnet} option default-router {subnet_prefix}.1")
    vyos(f"{subnet} option name-server {LAN_RESOLVER}")
    vyos(f"{subnet} option domain-name {LAN_DOMAIN_NAME}")
    vyos(f"{subnet} lease {LAN_LEASE_SECONDS}")
    vyos(f"{subnet} static-mapping {host} mac {mac}")
    vyos(f"{subnet} static-mapping {host} ip-address {address}")

DESCRIPTION = (
    "Healthy reference state for campus_small, campus_medium folded onto one firewall. "
    "Two layer-2 Arista access switches carry five VLANs (CORP, VOIP, BMS, INFRA, GUESTS) "
    "straight to a single VyOS firewall, the gateway of every VLAN. It filters between "
    "the user groups with intra-zone filtering, holds the DMZ (web, SFTP and DNS segments) "
    "and the Internet segment, enforces LAN-to-DMZ and egress policy, masquerades toward "
    "the Internet, serves the desks' DHCP reservations and the DMZ servers' leases, and "
    "shapes its two access trunks. dns-access-4 is the LAN resolver and forwards dmz.lab "
    "to dns-dmz, which is authoritative for it. On eth7 the firewall reaches a remote site "
    "through router-wan and wan-edge, a Linux router whose eth2 is a 10 Mbit circuit to "
    "srv-site, the iperf3 sink."
)


def write(name, identifier, sentence, with_qos_policy):
    state = commands + campus_site.site_commands(TOPOLOGY, with_qos_policy=with_qos_policy)
    payload = {"id": identifier, "description": f"{DESCRIPTION} {sentence}", "commands": state}
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for c in state:
        counts[c["mode"]] = counts.get(c["mode"], 0) + 1
    print(f"{path}: {len(state)} commands {counts}")


write("healthy.json", "campus_small_healthy",
      "wan-edge:eth2 carries the descriptor's two-class policy: CORP is guaranteed 8 Mbit of "
      "the circuit, everything else 2 Mbit. This is the state every family but "
      "assured_bandwidth starts from.", with_qos_policy=True)
write("healthy_greenfield.json", "campus_small_unshaped_site_healthy",
      "wan-edge:eth2 carries the circuit and no class policy: the link is first come, first "
      "served. This is the state assured_bandwidth starts from.", with_qos_policy=False)
