#!/usr/bin/env python3
"""Generate the healthy reference state for the sme01-dns lab.

A single VyOS firewall replaces the two SR Linux spines. It keeps everything
the spines did -- fabric transit and the DHCP service the leaves relay to --
and adds what a spine pair could not do: it is the policy enforcement point
between the INTERNAL, DMZ and OUTSIDE zones.

Access stays routed, exactly as before: every endpoint sits alone in its own
/24 behind a leaf interface, and the leaf relays its DHCP broadcast to the
firewall. The DMZ servers moved off leaf2 and are cabled straight to the
firewall, so every packet between a DMZ application and an internal endpoint
crosses the zone policy -- which is the point of the topology.

The client endpoints carry no address of their own: the lease carries the
address, the default gateway (option 3), the resolver (option 6) and the
search domain (option 15). The servers -- the two nameservers, web1, app1 --
stay statically addressed, which is how an SME actually runs: reservations for
the desks, fixed addresses for the machines other things point at.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
OUT = REPO / "benchmarks/testbeds/containerlab/sme01-dns/states/healthy.json"

INTERNAL_RESOLVER, PUBLIC_RESOLVER = "10.10.60.10", "10.10.41.10"

# --- hosts behind a leaf: routed access, one /24 per endpoint ---------------
# host -> (address, gateway, leaf, leaf interface, resolver)
# The internal resolver is not an endpoint -- it carries the `service` role in
# the abstract topology and is consumed, not evaluated -- but the leaf routes
# for it, so it belongs in this table.
LEAF_HOSTS = {
    "user1":    ("10.10.10.10/24",  "10.10.10.1", "leaf1", "ethernet-1/10", INTERNAL_RESOLVER),
    "dns-int":  ("10.10.60.10/24",  "10.10.60.1", "leaf1", "ethernet-1/11", None),
    "guest1":   ("10.10.20.10/24",  "10.10.20.1", "leaf1", "ethernet-1/20", INTERNAL_RESOLVER),
    "admin1":   ("10.10.99.100/24", "10.10.99.1", "leaf1", "ethernet-1/57", INTERNAL_RESOLVER),
    "finance1": ("10.10.30.10/24",  "10.10.30.1", "leaf2", "ethernet-1/30", INTERNAL_RESOLVER),
}

# --- hosts cabled straight to the firewall ---------------------------------
# host -> (address, gateway, firewall port, zone, resolver)
# No leaf in between: the firewall owns their gateway address, so their traffic
# inland has nowhere to go except through the zone policy.
FIREWALL_HOSTS = {
    "web1":      ("10.10.40.10/24",  "10.10.40.1",  "eth3", "DMZ",     INTERNAL_RESOLVER),
    "dns-dmz":   ("10.10.41.10/24",  "10.10.41.1",  "eth4", "DMZ",     None),
    "app1":      ("10.10.50.10/24",  "10.10.50.1",  "eth5", "DMZ",     INTERNAL_RESOLVER),
    # The outside client sits beyond the enterprise and uses the public
    # nameserver, not the internal resolver.
    "external1": ("203.0.113.10/24", "203.0.113.1", "eth6", "OUTSIDE", PUBLIC_RESOLVER),
}

# The endpoints that acquire their address by DHCP -> pinned MAC. The server
# keys its reservations on the hardware address, so the MAC is fixed in
# topology.clab.yml and repeated here; the two must agree.
DHCP_CLIENTS = {
    "user1":    "02:c1:ab:0a:0a:0a",
    "guest1":   "02:c1:ab:0a:14:0a",
    "finance1": "02:c1:ab:0a:1e:0a",
    "admin1":   "02:c1:ab:0a:63:64",
}
# Kea needs a unique numeric id per subnet; the VLAN number is a stable one.
DHCP_SUBNET_IDS = {"user1": 10, "guest1": 20, "finance1": 30, "admin1": 99}
DHCP_LEASE_SECONDS = 3600
# The stock BusyBox hook loses to containerlab's management default route and
# cannot write /etc/resolv.conf over its bind mount; dhcp/udhcpc.script says
# why in full. Mounted read-only by topology.clab.yml.
UDHCPC_SCRIPT = "/opt/dhcp/udhcpc.script"
DOMAIN_NAME = "sme01.internal"
PUBLIC_DOMAIN_NAME = "sme01.example"
# The DMZ applications live in the public zone and are reached by forwarding,
# not by a second copy in the internal zone -- so an internal client resolving
# `web1` has to try both namespaces. Option 119 carries the pair; option 15
# still carries the primary domain on its own, for clients that ignore 119.
SEARCH_DOMAINS = [DOMAIN_NAME, PUBLIC_DOMAIN_NAME]

# --- fabric ----------------------------------------------------------------
# One point-to-point link per leaf toward the firewall. A single firewall
# replaces the spine pair, so there is no second path and no ECMP: the
# redundancy the two spines provided is traded for a policy enforcement point.
# (node, interface, address, peer)
FABRIC = [
    ("firewall", "eth1", "10.255.0.0/31", "leaf1"),
    ("firewall", "eth2", "10.255.0.2/31", "leaf2"),
    ("leaf1", "ethernet-1/1", "10.255.0.1/31", "firewall"),
    ("leaf2", "ethernet-1/1", "10.255.0.3/31", "firewall"),
]
# The firewall's own end of each fabric link: what the leaf relays DHCP to,
# and what the leaf routes everything unknown through.
FIREWALL_NEXT_HOP = {"leaf1": "10.255.0.0", "leaf2": "10.255.0.2"}
# The leaf's end of each fabric link: how the firewall reaches what is behind.
LEAF_NEXT_HOP = {"leaf1": "10.255.0.1", "leaf2": "10.255.0.3"}
# Which prefixes live behind which leaf, and which hang off the firewall.
BEHIND_LEAF = {
    "leaf1": ["10.10.10.0/24", "10.10.20.0/24", "10.10.60.0/24", "10.10.99.0/24"],
    "leaf2": ["10.10.30.0/24"],
}
ON_FIREWALL = ["10.10.40.0/24", "10.10.41.0/24", "10.10.50.0/24", "203.0.113.0/24"]

# --- zones -----------------------------------------------------------------
# Three zones, and only three: the leaves aggregate every internal subnet onto
# two fabric links, so a zone cannot separate users from finance here. The
# boundary this topology exists to enforce is DMZ <-> INTERNAL, and that one is
# a zone boundary. Per-endpoint granularity comes from source-address matching
# inside the rulesets below.
ZONES = {"INTERNAL": [], "DMZ": [], "OUTSIDE": []}
# Everything reached through a leaf is INTERNAL, so the two fabric ports carry
# the whole internal estate between them.
for _node, _interface, _address, _peer in FABRIC:
    if _node == "firewall":
        ZONES["INTERNAL"].append(_interface)
for _host, (_a, _g, _port, _zone, _r) in FIREWALL_HOSTS.items():
    ZONES[_zone].append(_port)

WAN_PORT = next(port for _a, _g, port, zone, _r in FIREWALL_HOSTS.values()
                if zone == "OUTSIDE")
INTERNAL_SUBNETS = sorted({
    ".".join(gateway.split(".")[:3]) + ".0/24"
    for _a, gateway, _l, _i, _r in LEAF_HOSTS.values()
})

USER1, GUEST1, FINANCE1 = "10.10.10.10", "10.10.20.10", "10.10.30.10"
ADMIN1, DNS_INT = "10.10.99.100", "10.10.60.10"
WEB, DNS_DMZ, APP, EXTERNAL1 = "10.10.40.10", "10.10.41.10", "10.10.50.10", "203.0.113.10"

# Least-privilege matrix. A rule is
#   (protocol, source, destination, port, description)
# with None meaning "any". Every ruleset is default-action drop and opens with
# an established/related accept, so a zone pair listed here permits exactly
# what is enumerated and nothing else; a zone pair absent from the table has no
# ruleset at all and is denied by the zone model itself.
#
# ICMP echo is opened on every declared flow because the benchmark oracle
# probes reachability with ping. Filtering on ports alone would fail every
# connectivity check while the services themselves stayed reachable.
ANY = None
POLICY = {
    # What internal endpoints are allowed to ask of the DMZ applications, host
    # by host: user1 gets web and app, guest1 gets web only, finance1 gets app
    # only, and the resolver gets its forwarder. Nothing else crosses.
    ("INTERNAL", "DMZ"): [
        ("tcp",  USER1,    WEB,     80,   "user1-to-web"),
        ("icmp", USER1,    WEB,     None, "user1-ping-web"),
        ("tcp",  USER1,    APP,     5201, "user1-to-app"),
        ("icmp", USER1,    APP,     None, "user1-ping-app"),
        ("tcp",  GUEST1,   WEB,     80,   "guest1-to-web"),
        ("icmp", GUEST1,   WEB,     None, "guest1-ping-web"),
        ("tcp",  FINANCE1, APP,     5201, "finance1-to-app"),
        ("icmp", FINANCE1, APP,     None, "finance1-ping-app"),
        # The split-horizon forward: dns-int is authoritative for the internal
        # zone and hands sme01.example to the DMZ nameserver.
        ("udp",  DNS_INT,  DNS_DMZ, 53,   "resolver-forward-udp"),
        ("tcp",  DNS_INT,  DNS_DMZ, 53,   "resolver-forward-tcp"),
        # The administration station reaches everything.
        (ANY,    ADMIN1,   None,    None, "admin-to-dmz"),
    ],
    # A DMZ server never initiates a session inland except to resolve a name.
    # The ICMP rules exist only because the oracle probes every required flow
    # in both directions; a service-aware oracle would let them go away.
    ("DMZ", "INTERNAL"): [
        ("udp",  WEB, DNS_INT,  53,   "web1-resolve-udp"),
        ("tcp",  WEB, DNS_INT,  53,   "web1-resolve-tcp"),
        ("udp",  APP, DNS_INT,  53,   "app1-resolve-udp"),
        ("tcp",  APP, DNS_INT,  53,   "app1-resolve-tcp"),
        ("icmp", APP, DNS_INT,  None, "app1-ping-resolver"),
        ("icmp", WEB, USER1,    None, "web1-ping-user1"),
        ("icmp", APP, USER1,    None, "app1-ping-user1"),
        ("icmp", WEB, GUEST1,   None, "web1-ping-guest1"),
        ("icmp", APP, FINANCE1, None, "app1-ping-finance1"),
    ],
    # The Internet gets the published web service and the public nameserver.
    # It gets nothing else, and no ruleset at all toward INTERNAL.
    ("OUTSIDE", "DMZ"): [
        ("tcp",  EXTERNAL1, WEB,     80,   "internet-to-web"),
        ("icmp", EXTERNAL1, WEB,     None, "internet-ping-web"),
        ("udp",  EXTERNAL1, DNS_DMZ, 53,   "internet-resolve-udp"),
        ("tcp",  EXTERNAL1, DNS_DMZ, 53,   "internet-resolve-tcp"),
        ("icmp", EXTERNAL1, DNS_DMZ, None, "internet-ping-dns"),
    ],
    ("DMZ", "OUTSIDE"): [
        ("icmp", WEB, EXTERNAL1, None, "web1-ping-outside"),
    ],
    # Internal users reach the Internet on web ports, translated on the way out.
    ("INTERNAL", "OUTSIDE"): [
        ("tcp",  None,   None, 80,   "internal-web-out"),
        ("tcp",  None,   None, 443,  "internal-tls-out"),
        ("icmp", None,   None, None, "internal-ping-out"),
        (ANY,    ADMIN1, None, None, "admin-to-outside"),
    ],
}
# Return paths that carry no new session, only replies. This is the deny that
# matters most: nothing on the Internet can open a session toward the LAN.
REPLY_ONLY = [("OUTSIDE", "INTERNAL")]

commands = []


def add(target, mode, command):
    commands.append({"target": target, "mode": mode, "command": command})


def srl(target, command):
    add(target, "srl_cli", command)


def vyos(command):
    add("firewall", "vyos_cli", command)


def routed_interface(node, interface, address):
    srl(node, f"set / interface {interface} admin-state enable")
    srl(node, f"set / interface {interface} subinterface 0 admin-state enable")
    srl(node, f"set / interface {interface} subinterface 0 ipv4 admin-state enable")
    srl(node, f"set / interface {interface} subinterface 0 ipv4 address {address}")
    srl(node, f"set / network-instance default interface {interface}.0")


def host_addressing(host, address, gateway, resolver):
    """Address one statically configured host, or start its DHCP client."""
    add(host, "shell", "ip link set eth1 up")
    if host in DHCP_CLIENTS:
        # apply_state() runs every shell command before the first SR Linux
        # command and every VyOS command last (benchmarks/platforms/containerlab/env.py),
        # so a one-shot DHCP client here would always lose the race against
        # the fabric and the server it needs. udhcpc is therefore started with
        # -b: it forks into the background and keeps retrying until the relays
        # and the firewall are up. That is also what a real client does, and it
        # leaves a daemon running to renew the lease.
        pidfile = "/var/run/udhcpc.eth1.pid"
        # Kill a client left over from an earlier apply before starting one,
        # so re-applying the state does not stack daemons on the interface.
        add(host, "shell",
            f"sh -c 'kill $(cat {pidfile} 2>/dev/null) 2>/dev/null; "
            f"udhcpc -i eth1 -b -p {pidfile} -s {UDHCPC_SCRIPT} "
            f"-O search -t 4 -T 2 -A 3 -x hostname:{host}'")
        return
    add(host, "shell", f"ip address replace {address} dev eth1")
    add(host, "shell", f"ip route replace default via {gateway}")
    if resolver:
        # The DHCP clients get a search domain from option 15; a statically
        # configured host has to be told the same thing explicitly, or short
        # names work on the desks and fail on the servers. Which namespace
        # depends on which side of the split horizon the host sits on.
        search = (" ".join(SEARCH_DOMAINS) if resolver == INTERNAL_RESOLVER
                  else PUBLIC_DOMAIN_NAME)
        # Idempotent: the file is rewritten, not appended to.
        add(host, "shell",
            f"sh -c 'printf \"search {search}\\nnameserver {resolver}\\n\" > /etc/resolv.conf'")


# --- hosts -----------------------------------------------------------------
for host, (address, gateway, _, _, resolver) in LEAF_HOSTS.items():
    host_addressing(host, address, gateway, resolver)
for host, (address, gateway, _, _, resolver) in FIREWALL_HOSTS.items():
    host_addressing(host, address, gateway, resolver)

# --- DNS servers: install the reference zone files and reload -------------
# The sources are mounted read-only under /opt/dns-src, so the running config in
# /etc/bind stays writable: a fault can edit it, and the restore is a copy back.
DNS_FILES = {
    "dns-int": ["named.conf.options", "named.conf.local", "db.sme01.internal"],
    "dns-dmz": ["named.conf.options", "named.conf.local", "db.sme01.example"],
}
for server, files in DNS_FILES.items():
    for name in files:
        add(server, "shell", f"cp /opt/dns-src/{name} /etc/bind/{name}")
    add(server, "shell", "rndc reload")

# --- fabric ----------------------------------------------------------------
for node, interface, address, peer in FABRIC:
    if node == "firewall":
        vyos(f"set interfaces ethernet {interface} address {address}")
        vyos(f"set interfaces ethernet {interface} description fabric-to-{peer}")
    else:
        routed_interface(node, interface, address)

# --- leaf access interfaces ------------------------------------------------
# Access is routed: every endpoint sits alone in its own /24 behind a leaf
# interface, so a DHCP broadcast never reaches the firewall on its own. The
# leaf relays it, using the gateway address as gi-address -- which is also what
# tells the server which reservation the request belongs to.
for host, (address, gateway, leaf, interface, _) in LEAF_HOSTS.items():
    prefix_len = address.split("/")[1]
    routed_interface(leaf, interface, f"{gateway}/{prefix_len}")
    if host not in DHCP_CLIENTS:
        continue
    srl(leaf, f"set / interface {interface} subinterface 0 ipv4 dhcp-relay admin-state enable")
    srl(leaf, f"set / interface {interface} subinterface 0 ipv4 dhcp-relay gi-address {gateway}")
    srl(leaf, f"set / interface {interface} subinterface 0 ipv4 dhcp-relay server [ {FIREWALL_NEXT_HOP[leaf]} ]")

# --- leaf static routing ---------------------------------------------------
# One next hop now, not two: everything the leaf does not own itself goes to
# the firewall.
for leaf in ("leaf1", "leaf2"):
    other = "leaf2" if leaf == "leaf1" else "leaf1"
    group = "to-firewall"
    srl(leaf, f"set / network-instance default next-hop-groups group {group} nexthop 1 ip-address {FIREWALL_NEXT_HOP[leaf]} admin-state enable")
    for prefix in BEHIND_LEAF[other] + ON_FIREWALL:
        srl(leaf, f"set / network-instance default static-routes route {prefix} next-hop-group {group} admin-state enable")

# --- firewall: gateway interfaces for the directly attached segments -------
for host, (_, gateway, port, _, _) in FIREWALL_HOSTS.items():
    vyos(f"set interfaces ethernet {port} address {gateway}/24")
    vyos(f"set interfaces ethernet {port} description {host}-gateway")

# --- firewall: static routes toward what sits behind the leaves ------------
for leaf, prefixes in BEHIND_LEAF.items():
    for prefix in prefixes:
        vyos(f"set protocols static route {prefix} next-hop {LEAF_NEXT_HOP[leaf]}")

# --- firewall: DHCP service ------------------------------------------------
# The firewall took the service over from the spine pair. Every client subnet
# is remote -- it lives behind a leaf, one endpoint per /24 -- so none of them
# is on a broadcast interface of the firewall's own. Kea refuses that
# configuration outright unless it is told where the relayed packets land:
# listen-address is the firewall's end of each fabric link, and it is what
# turns "no subnet is connected to any interface" from a commit error into a
# working relay setup. Kea then picks the subnet from the relay's gi-address.
for address in FIREWALL_NEXT_HOP.values():
    vyos(f"set service dhcp-server listen-address {address}")

# Reservations, not a pool: the connectivity oracle pings addresses taken from
# the descriptor and the internal zone holds fixed A records, so a desk has to
# come back on the same address every time.
for host, mac in DHCP_CLIENTS.items():
    address, gateway, _, _, resolver = LEAF_HOSTS[host]
    network = ".".join(gateway.split(".")[:3]) + ".0/24"
    subnet = f"set service dhcp-server shared-network-name {host.upper()} subnet {network}"
    vyos(f"{subnet} subnet-id {DHCP_SUBNET_IDS[host]}")
    vyos(f"{subnet} option default-router {gateway}")
    vyos(f"{subnet} option name-server {resolver}")
    vyos(f"{subnet} option domain-name {DOMAIN_NAME}")
    for domain in SEARCH_DOMAINS:
        vyos(f"{subnet} option domain-search {domain}")
    vyos(f"{subnet} lease {DHCP_LEASE_SECONDS}")
    vyos(f"{subnet} static-mapping {host} mac {mac}")
    vyos(f"{subnet} static-mapping {host} ip-address {address.split('/')[0]}")

# --- firewall: zones -------------------------------------------------------
for zone, interfaces in ZONES.items():
    vyos(f"set firewall zone {zone} description {zone.lower()}-zone")
    for interface in interfaces:
        vyos(f"set firewall zone {zone} interface {interface}")

# --- firewall: least-privilege zone policy ---------------------------------
def emit_ruleset(from_zone, to_zone, rules):
    name = f"{from_zone}-TO-{to_zone}"
    vyos(f"set firewall ipv4 name {name} default-action drop")
    vyos(f"set firewall ipv4 name {name} description {from_zone.lower()}-to-{to_zone.lower()}")
    # Replies to sessions this firewall already allowed always come back.
    vyos(f"set firewall ipv4 name {name} rule 10 action accept")
    vyos(f"set firewall ipv4 name {name} rule 10 description return-traffic")
    vyos(f"set firewall ipv4 name {name} rule 10 state established")
    vyos(f"set firewall ipv4 name {name} rule 10 state related")
    for index, (protocol, source, destination, port, description) in enumerate(rules, start=1):
        rule = 10 * (index + 1)
        prefix = f"set firewall ipv4 name {name} rule {rule}"
        vyos(f"{prefix} action accept")
        vyos(f"{prefix} description {description}")
        if protocol:
            vyos(f"{prefix} protocol {protocol}")
        if protocol == "icmp":
            vyos(f"{prefix} icmp type-name echo-request")
        if source:
            vyos(f"{prefix} source address {source}")
        if destination:
            vyos(f"{prefix} destination address {destination}")
        if port:
            vyos(f"{prefix} destination port {port}")
    vyos(f"set firewall zone {to_zone} from {from_zone} firewall name {name}")


for (from_zone, to_zone), rules in POLICY.items():
    emit_ruleset(from_zone, to_zone, rules)
for from_zone, to_zone in REPLY_ONLY:
    emit_ruleset(from_zone, to_zone, [])

# --- firewall: NAT ---------------------------------------------------------
# Source NAT: the internal subnets are translated behind the firewall's own
# OUTSIDE address on their way to the Internet. The DMZ is not translated --
# its servers are reached on their own addresses.
for index, subnet in enumerate(INTERNAL_SUBNETS, start=1):
    rule = 100 + index
    vyos(f"set nat source rule {rule} description masquerade-{subnet.split('/')[0]}")
    vyos(f"set nat source rule {rule} outbound-interface name {WAN_PORT}")
    vyos(f"set nat source rule {rule} source address {subnet}")
    vyos(f"set nat source rule {rule} translation address masquerade")

# Destination NAT: the web service is published on the firewall's public
# address, which is how a DMZ server is normally reachable from the Internet.
vyos("set nat destination rule 10 description publish-web")
vyos(f"set nat destination rule 10 inbound-interface name {WAN_PORT}")
vyos("set nat destination rule 10 protocol tcp")
vyos("set nat destination rule 10 destination port 80")
vyos(f"set nat destination rule 10 translation address {WEB}")

payload = {
    "id": "sme01_dns_firewall_dhcp_healthy",
    "description": (
        "Healthy reference state for SME01-dns. A single VyOS firewall replaces the two "
        "SR Linux spines: it is the fabric transit router, the DHCP server the leaves relay "
        "to, and the policy enforcement point between the INTERNAL, DMZ and OUTSIDE zones. "
        "Access stays routed -- one /24 per endpoint behind a leaf interface -- while the "
        "DMZ servers (web1, app1, dns-dmz) and external1 are cabled straight to the "
        "firewall, so every packet between a DMZ application and an internal endpoint "
        "crosses the zone policy. The client endpoints (user1, guest1, finance1, admin1) "
        "acquire their address, default gateway, resolver and search domain from the "
        "firewall's DHCP reservations, relayed by the leaves; the servers stay statically "
        "addressed. Rulesets are default-action drop and filter on source address, "
        "destination address and port; OUTSIDE gets no ruleset toward INTERNAL at all. "
        "ICMP echo is opened on every declared flow because the benchmark oracle probes "
        "reachability with ping."
    ),
    "commands": commands,
}

OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
counts = {}
for c in commands:
    counts[c["mode"]] = counts.get(c["mode"], 0) + 1
print(f"{OUT}: {len(commands)} commands {counts}")
