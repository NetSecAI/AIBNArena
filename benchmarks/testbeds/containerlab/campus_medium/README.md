# campus_medium ContainerLab Topology

The reference campus lab (`containerlab-sw-fw`), with its two firewalls
merged into one (2026-10-28): access and aggregation switches, and a single VyOS
firewall that is the gateway of every LAN VLAN and holds the DMZ and the Internet.
The switches are the original's Arista cEOS and its fabric -- every access switch
dual-homed by LACP to an MLAG pair of aggregates, the firewall's `bond0` over the
same pair -- and the policies, the DHCP service, the resolvers and the address
plan are the original's too.

The executable descriptor is `scenarios/topologies/campus_medium.yaml`.

One addition is not the original's: a remote site behind the firewall,
reached over a shaped WAN (see [The remote site](#the-remote-site)). It is what
lets the routing faults and the QoS families run on this lab.

| | original lab | `campus_medium` |
|---|---|---|
| Switches | 6 × Arista cEOS | 6 × Arista cEOS 4.36.1F, pure layer 2 |
| Aggregation | MLAG pair, peer-link, LACP to every access switch | the same |
| Firewalls | firewall-interne (LAN) and firewall-DMZ-internet (DMZ, Internet), a `/30` transit between them | one, `firewall`, holding both (see [One firewall](#one-firewall)) |
| Firewall → LAN | `bond0` (LACP) over the MLAG pair | the same, on `eth1` + `eth2` |
| Internet | `br-untrust` host bridge, real Internet | `external1`, a container on the same segment |
| LAN desks | DHCP from a dynamic range | DHCP reservations on pinned MACs |
| DMZ servers | DHCP static-mappings on the DMZ firewall | static addresses, default route and resolver in `states/healthy.json` |
| Switch SVIs | one per VLAN, unused | the MLAG peer SVI only (VLAN 4094) |
| Configuration | startup-config files | `states/healthy.json`, applied after deployment; `ceos/startup.cfg` only takes the switches out of ZTP |
| Node kinds | `arista_ceos`, `vyosnetworks_vyos`, `linux` | `arista_ceos`, `vyos`, `linux` |
| Remote site | none | router-wan, wan-edge, srv-site on the firewall's `eth7` |

## Layout

```
                     external1 192.168.122.1                 (the Internet)
                           │ eth6 192.168.122.202
  web-dmz  ── eth3 ┐       │
  sftp-dmz ── eth4 ├──── firewall ── eth7 ── router-wan ── wan-edge ══10 Mbit══ srv-site
  dns-dmz  ── eth5 ┘       │   (every zone, NAT, DHCP, QoS)
                           │ bond0 = eth1 + eth2 (LACP)      gateway of every VLAN
                    Et1 ┌──┴────────┐ Et1   Po1, MLAG 1
          switch-aggregate-1 ═══ switch-aggregate-2          MLAG pair: Po999 = Et6 + Et7, VLAN 4094
            Et2..Et5 │                   │ Et2..Et5        Po2..Po5, MLAG 2..5
                     └───┬─────┬─────┬───┘
                 Et1+Et2 │     │     │    │                 every access switch: Po1 = Et1 (agg-1) + Et2 (agg-2)
       switch-access-1  switch-access-2  switch-access-3  switch-access-4
                Et3│           Et3│           Et3│      Et3│    │Et4
       host-access-1   host-access-2   host-access-3  host-access-4  dns-access-4
```

| VLAN | Group | Subnet | Gateway (`firewall`) | Access switch | Node | Address |
|---|---|---|---|---|---|---|
| 100 | CORP | 10.0.0.0/24 | `bond0.100` 10.0.0.1 | switch-access-1 | host-access-1 | 10.0.0.150, DHCP |
| 110 | VOIP | 10.0.1.0/24 | `bond0.110` 10.0.1.1 | switch-access-2 | host-access-2 | 10.0.1.150, DHCP |
| 120 | BMS | 10.0.2.0/24 | `bond0.120` 10.0.2.1 | switch-access-3 | host-access-3 | 10.0.2.150, DHCP |
| 130 | INFRA | 10.0.3.0/24 | `bond0.130` 10.0.3.1 | switch-access-4 | dns-access-4 | 10.0.3.53, static |
| 140 | GUESTS | 10.0.4.0/24 | `bond0.140` 10.0.4.1 | switch-access-4 | host-access-4 | 10.0.4.150, DHCP |

Every VLAN reaches the firewall through both aggregates: the access switch's
`Port-Channel1` carries its VLANs to the pair, the pair's `Port-Channel1` (MLAG 1)
all five to `bond0`, and the peer-link all of them between the two aggregates. One
cable, or one whole aggregate, can go without a VLAN losing its path.

| DMZ / Internet | `firewall` | Node | Address |
|---|---|---|---|
| 172.16.2.0/24 | `eth3` 172.16.2.254 | web-dmz (nginx) | 172.16.2.10, static |
| 172.16.1.0/24 | `eth4` 172.16.1.254 | sftp-dmz | 172.16.1.10, static |
| 172.16.3.0/24 | `eth5` 172.16.3.254 | dns-dmz | 172.16.3.53, static |
| 192.168.122.0/24 | `eth6` 192.168.122.202 | external1 | 192.168.122.1, static |

These are campus_small's ports, `eth7` (the remote site) included: the firewall of
the three campus labs differs only on its LAN side.

## What changed from the original, and why

**The switches.** Pure layer 2, configured by the reference state rather than by
startup-config files: `ceos/startup.cfg` only takes cEOS out of Zero Touch
Provisioning, which otherwise holds every port routed and keeps polling for a
configuration server. Everything else -- the VLANs, the port-channels, the MLAG
domain, the access ports -- is rendered from the descriptor's interfaces and
segments by [`benchmarks/testbeds/campus_fabric.py`](../../campus_fabric.py), so the
declaration the compiler reasons over and the configuration the switches hold
cannot drift apart; `test_campus_testbeds.py` also checks every declared port
against the containerlab cabling. The VLANs keep the original's names
(`VLAN_100`...), the MLAG domain its id (`CAMPUS_MLAG`), the peer-link its trunk
group (`MLAGPEER`) and its addresses (172.31.0.1/30 and .2). The access ports carry
no `spanning-tree portfast`: the default (`portfast auto`) already makes a port that
hears no BPDU an edge port within seconds.

**`bond0`'s shaper.** `QOS-UPLINK`, the original's single policy on `bond0`, with
its four classes on the vifs that carry them (CORP, VOIP, BMS, GUESTS).

**Zone names.** Upper-cased, so every original ruleset name reads `<FROM>-<TO>`
(`TRUST-DMZ`, `TRUST-UNTRUST`, ...), which is the descriptor's
`zone_ruleset_template`. The original's second `untrust` zone, firewall-interne's
end of the transit, went with the transit (see [One firewall](#one-firewall)).

**DHCP.** The oracle pings fixed addresses, so each desk has a reservation on a
pinned MAC (`02:00:0a:00:0X:96` spells 10.0.X.150, the first address of the
original's range) instead of a dynamic lease. A pool rebuilt by a DHCP fault's
restore carries `domain-name` and `lease`, so the LAN pools gained both
(`campus.lab`, 86400 s).

**Only the desks lease.** Since 2026-11-06 web-dmz and sftp-dmz hold their address,
default route, resolver and `dmz.lab` search domain from the reference state, as
dns-dmz and dns-access-4 do. The original's DMZ pools, the shared-network `DMZ`, its
static-mappings, `DMZ-LOCAL`'s DHCP rule and the two servers' pinned MACs went with
their leases. A lease renewal rewrites a client's address, link and default route,
and the judge and the ANI both renew, so a fault on those was repaired by the bench
in the agent's place (the intent translation report of main01). The dated sections
below predate this.

**The Internet.** `br-untrust` was a host bridge that had to be created outside
containerlab before every deployment, and the lifecycle only runs
`containerlab deploy`. `external1` stands on the same segment at the upstream
gateway address instead, with forwarding disabled: it is where the Internet
ends, and as a router it bounced every packet for another public address back
to the firewall until the TTL ran out.

**DNS.** Both bind9 servers drop the original's global forward to 8.8.8.8:
nothing answers there, and a global forwarder makes BIND skip its built-in
empty RFC 1918 reverse zones. Every reverse lookup then waited on the dead
forwarder; `ping` makes one per reply when given a name, and the clients'
retries on SERVFAIL turned each 2 s name check into 6 s. The conditional
forward of `dmz.lab` from dns-access-4 to dns-dmz is unchanged. Not the
original's either: dns-access-4 is authoritative for `campus.lab`, the domain the
LAN pools hand out, with one record per LAN endpoint at its reserved address
(`dns/access/db.campus.lab`). The DHCP oracles have a leased desk resolve its own
name there, fully qualified -- containerlab writes every node's short name into
each container's `/etc/hosts`, which answers a bare name whatever DHCP and DNS do.

**Images.** All Linux nodes but the resolvers run `network-multitool`. The
original's `alpine`, `nginx:alpine` and `openssh-server` images are BusyBox
builds without `iproute2` or `tc`, which the observer and the ANI read on
every Linux node. web-dmz still serves HTTP — the reference
state starts the image's nginx — but sftp-dmz runs no SSH daemon: it holds the
`SRV-FTP` address the rules name, and nothing listens on port 22.

**Switch SVIs.** The original's switches had an address in each of their VLANs.
They answered no gateway and are not carried over: a switch that routed would be
a way around the firewall, and the management plane is containerlab's own.
The MLAG peer SVI (`Vlan4094`) stays: it carries the pair's keepalives, on the
peer-link alone.

## The remote site

Three nodes off the firewall's `eth7`, shared by the three campus labs and
rendered from the descriptor by [`benchmarks/testbeds/campus_site.py`](../../campus_site.py):

| Node | Kind | Interface | Address | Role |
|---|---|---|---|---|
| firewall | VyOS | `eth7` | 10.255.254.0/31 | zone `SITE` |
| router-wan | VyOS | `eth1` / `eth2` | 10.255.254.1/31, 10.255.254.2/31 | a plain router, no zone |
| wan-edge | Linux | `eth1` / `eth2` | 10.255.254.3/31, 10.1.0.1/24 | `eth2` is the 10 Mbit circuit |
| srv-site | Linux | `eth1` | 10.1.0.10/24 | iperf3 sinks on 5201 (measurement) and 5202 (load) |

It exists for two scenario families the original cannot carry:

- **`wrong_routing_table`.** A route fault rewrites the route a router holds toward a
  remote destination. The firewall routes `10.1.0.0/24` explicitly, through
  router-wan, which is a second router a loop can be made with (m4). Its default
  route goes to external1, where the site's traffic dies: a route made unusable
  (m3) is not rescued by a shorter one. Every route a router holds is declared under its `static_routes` in
  the descriptor, which the generator renders and the faults bind to and restore.
- **QoS.** wan-edge's `eth2` carries the descriptor's `qos_policy`: an HTB circuit of
  10 Mbit, CORP (`10.0.0.0/24`) guaranteed 8 Mbit and everything else 2. The measured
  flow is host-access-1 → srv-site, the contending one host-access-3 → srv-site.
  `healthy_greenfield.json` keeps the circuit and drops the classes: it is the state
  `assured_bandwidth` starts from (`benchmarks/configs/testbeds/campus_medium-greenfield.toml`).

On the firewall the site is zone `SITE`: `TRUST-SITE` lets CORP and BMS through
(the measured flow is TCP, the load UDP) and every group ping, `SITE-LOCAL` lets the
site ping the firewall, and no ruleset goes from `SITE` toward `TRUST`. The NAT
translates only what leaves by `eth6`, toward the Internet, so the site's traffic
reaches the shaper with its LAN source, which the shaper classifies by.

Every Linux node of the lab runs at MTU 1500 (`ip link set eth1 mtu 1500` in the
reference state): containerlab brings veths up at 9500, and a jumbo endpoint behind a
hop that does not carry jumbo frames blackholes PMTU discovery, which makes a
throughput measurement meaningless (sme01-qos measured it).

## One firewall

The original's two firewalls had disjoint responsibilities: firewall-interne the
gateway of every VLAN, the filter between the user groups, the desks' DHCP and the
LAN shaper; firewall-DMZ-internet the DMZ, the Internet, LAN egress policy, the
only NAT and the DMZ servers' DHCP, with a `/30` transit between them. They are
one box since 2026-10-28, as on campus_small: a second firewall made every LAN
egress path two routed hops, added a transit zone and its accept-all ruleset,
and needed a discard route for `10.0.0.0/16` on firewall-interne, without
which a destination inside the `/16` but in no connected `/24` bounced between the
two until its TTL ran out. The agents took that route for the fault (see the E19
records), and it guarded nothing once the loop was gone; it went with the merge.
What merging changed:

- **No transit.** The `/30`, firewall-interne's `TRANSIT` zone and its
  `TRUST-TRANSIT` and `TRANSIT-LOCAL` rulesets are gone: LAN egress meets
  `TRUST-DMZ` and `TRUST-UNTRUST` on the box that routes it. Every other ruleset
  is the original's (with the least-privilege changes below), rule for rule.
- **One `SRV-DNS` per box.** Each original firewall had an address-group
  `SRV-DNS`, naming its own resolver. The LAN resolver keeps `SRV-DNS`
  (`TRUST-INTRA` rules 30/35/40); the DMZ one is `SRV-DNS-DMZ` (`TRUST-DMZ`
  rules 60/65, `DMZ-INTRA` rules 10/15).
- **`TRUST-LOCAL` is firewall-interne's** -- DHCP and ping -- since this box
  serves the desks' leases.
- **Ports.** `bond0` is `eth1` + `eth2`; the DMZ, the Internet and the remote site
  are on campus_small's `eth3` to `eth7`.

The policy, zone by zone:

- **Between the user groups.** The five VLANs share one zone, so their traffic is
  intra-zone and `TRUST-INTRA` applies through `intra-zone-filtering`: CORP
  reaches BMS on HTTPS (tcp/443) and BACnet/IP (udp/47808) and VOIP on SIP
  (udp/5060), and pings both; every group reaches the resolver on port 53 and
  pings it; nothing else.
- **Leaving the LAN.** LAN → DMZ (`TRUST-DMZ`), LAN → Internet (`TRUST-UNTRUST`),
  DMZ → Internet (`DMZ-UNTRUST`) and LAN → site (`TRUST-SITE`), and the only
  NAT, toward the Internet. No ruleset exists from `DMZ`, `UNTRUST` or `SITE`
  toward `TRUST`, nor from `UNTRUST` toward `DMZ`: the zone model denies them.
  Inside the DMZ zone, `DMZ-INTRA` lets the segments reach only the DMZ
  resolver (port 53) and ping each other.

**Not the original's, for the HLD's least privilege** (2026-10-28). The
original's `TRUST-INTRA` let CORP open anything toward BMS and VOIP (rules 10
and 20) and every group ping every other (rule 40), and its DMZ segments
reached each other unfiltered. `TRUST-INTRA` now names the services above
(no RTP range: least privilege names single ports), and `DMZ-INTRA` is new. Measured on the cEOS lab after the change, with the
two firewalls: the 21 healthy checks at 0 % loss; CORP → BMS tcp/443 open and
tcp/8080 refused; GUESTS → CORP and VOIP → BMS pings refused; web-dmz and sftp-dmz
resolve `dmz.lab` through dns-dmz, and sftp-dmz → web-dmz tcp/80 times out.

The firewall drops containerlab's management default route at deployment
(`exec` in `topology.clab.yml`): it is a kernel route, it beats the VyOS static
default in the FIB, and traffic would otherwise leave through `eth0`.

The `MGMT` zone is the original's: containerlab's `eth0`, reachable on ssh, the
HTTPS API and ICMP (`MGMT-LOCAL`). As in the original, the reference state
declares `eth0` in the configuration — a reader of the configuration only
recognises a management port the configuration names — with
`ipv6 address no-default-link-local`, without which the commit fails: the
management network is IPv4-only and the kernel has IPv6 off on that port.

## Verified against the original

Measured on the two-firewall lab, before 2026-10-28: the comparison still holds for
the merged firewall's rulesets, groups, NAT and DHCP, which [One firewall](#one-firewall)
lists every difference from.

**Configuration.** Each original `config.boot` parsed into `set` paths and
compared with what is committed on the live firewall (`show configuration
commands`), leaving out containerlab's template (PKI, HTTPS API, login, NTP,
syslog, console). Every one of the reference state's 154 and 178 commands is
committed. firewall-DMZ-internet differs from the original only by the zone
names and three interface descriptions: rulesets, groups, NAT, routes, DHCP and
conntrack helpers are identical line for line. firewall-interne differs by the
changes listed above and nothing else: the zone renames and the DHCP
reservations. (Verified with the SR Linux port, which then had `bond0` split into
two trunks; `bond0` and `QOS-UPLINK` are the original's again since.)

**Behaviour.** 67 probes with real listeners on every probed port, each
expectation read from the original rulesets rather than from the generator, all
as the original says:

| Ruleset | Probed |
|---|---|
| `TRUST-INTRA` | the 12 ordered pairs of user groups in TCP (only CORP → BMS and CORP → VOIP open) and in ICMP (all open, rule 40); DNS to the resolver over UDP and TCP from every group |
| `TRUST-LOCAL` | ICMP to the gateway open, ssh to it dropped |
| `TRUST-DMZ` | ftp and ssh to `SRV-FTP` only, NFS and HTTP to any DMZ host, 443/8080 dropped, DNS UDP/TCP to `SRV-DNS` |
| `TRUST-UNTRUST`, `DMZ-UNTRUST` | 80, 443, 8080, 53 and ICMP open, 22 dropped; LAN and DMZ sources both seen by `external1` as `192.168.122.202` (masquerade) |
| zone model | UNTRUST → TRUST, UNTRUST → DMZ, DMZ → TRUST denied in ICMP and TCP |
| `DMZ-LOCAL` | ICMP to the gateway open, ssh dropped |
| routing | `10.0.5.5` blackholed on firewall-interne (the discard route went with the merge) |

**Switches** (cEOS, 2026-10-24). MLAG `active` and `connected` on both
aggregates, its five bundles `active-full`; every access switch's `Port-Channel1`
with both members active; `bond0` on firewall-interne in 802.3ad with both members
in one aggregator. Each desk's MAC is learnt on its access port, firewall-interne's
on `Port-Channel1` of the pair.

**QoS.** `tc` on `bond0`: root 1 Gbit, CORP 500 Mbit prio 2, VOIP 150 Mbit prio 1,
BMS 100 Mbit prio 4, GUESTS 250 Mbit prio 5, default 10 Mbit prio 6. VOIP's
`ceiling 100%` resolves against the interface speed, as it does on the original.

## Files

- `topology.clab.yml`: containers, links, pinned MACs, management network `172.27.0.0/24`;
- `states/generate_healthy_state.py`: generator — edit its tables, not the JSON. Routes,
  the shaping policy and the iperf3 sinks are read from the descriptor;
- `states/healthy.json`: 653 commands (65 `shell`, 261 `eos_cli`, 327 `vyos_cli`);
- `states/healthy_greenfield.json`: the same without wan-edge's class policy (651 commands);
- `ceos/startup.cfg`: the switches' startup configuration, which only disables Zero Touch Provisioning;
- `dhcp/udhcpc.script`: the DHCP client hook, copied from `sme01-dns`, which says why the BusyBox one cannot be used;
- `dns/access/`, `dns/dmz/`: bind9 sources, mounted read-only and copied into `/etc/bind` by the reference state, so a fault can edit the running copy. `db.campus.lab` holds the LAN's names; `test_campus_testbeds.py` checks it against the descriptor's `hosts`.

## Deploy, apply and verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/campus_medium/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/campus_medium/states/healthy.json \
  --topology scenarios/topologies/campus_medium.yaml \
  --verify --verify-timeout 300
```

The figures in this section were measured on the two-firewall lab; the merged one's
deploy and reference-state times are under [Validated fault methods](#validated-fault-methods).

On cEOS (2026-10-24, alone on the host): deploy 181 s, the reference state applied
and verified in 232 s, `verify=True`. The switches take most of it: the cEOS
containers boot in about 90 s, and the MLAG pair negotiates once both aggregates
hold their configuration.

The figures below were measured on the SR Linux port. With the remote site
(2026-10-23): deploy 341 s and the reference state applied
and verified in 223 s, while `campus_small` and `campus_large` deployed alongside;
`verify=True` on every required flow, the site's two included. The health check
now runs 13 address and 8 name checks: the `campus.lab` names put the desks among
the destinations a name is pinged for.

Before the remote site, observed on a fresh deployment (2026-10-23): `verify=True`, the 11 address
checks and 5 name checks at 0 % loss. Containerlab waits for both VyOS nodes to
report healthy, so the state can be applied as soon as the deploy returns.
Through the Judge's own path (`ContainerlabPlatform.deploy_or_reset` on
`benchmarks/configs/testbeds/campus_medium.toml`, then `apply_reference_state`),
four fresh deployments: deploy 91–95 s (124 s while `campus_small` deployed
alongside), reference state applied and converged in 163–171 s, 38 command
groups, none failed.

One observation of the lab takes about 50 s: the pings in sequence, plus
`show version` on the six switches. The testbed config allows 300 s for the
reference state to converge.

Also measured on the healthy lab, beyond what the oracle pings (with two firewalls;
the policy is the same on one):

```
external1 -> host-access-1, external1 -> web-dmz      blocked (UNTRUST has no ruleset inland)
web-dmz -> host-access-1, sftp-dmz -> host-access-3   blocked (DMZ -> TRUST has no ruleset)
host-access-1 -> web-dmz:80, host-access-4 -> web-dmz:80   open (TRUST-DMZ rule 50)
host-access-1 -> host-access-3 tcp                     open    (TRUST-INTRA rule 10; tcp/443 only since 2026-10-28)
host-access-4 / -2 / -3 -> host-access-1 tcp           blocked (TRUST-INTRA default drop)
S>* 0.0.0.0/0 on the firewall                         installed in the FIB
```

**Never `docker restart` a lab node**: containerlab creates the veth pairs at
deploy time and a restart loses them. Redeploy instead.

## Validated fault methods

**Replayed on 2026-10-28 with one firewall** (pistes_e20 B15), after the merge and
the blackhole's removal. The 32 methods of the campaign domains, live on one
deployment of the cEOS lab (the experiment files' seeds), through the Judge's own
pieces without a redeploy between methods: the healthy oracle, the fault injected and
the expected-degradation oracle evaluated with the lifecycle's re-injection loop, the
fault's restore replayed and convergence awaited, then the repair and preservation
oracles. 32 of 32 passed every oracle, each fault seen on its first injection, in 100
minutes (deploy 137 s, reference state applied and verified in 215 s). The running
configuration of every device was also compared with the reference after each
restore. Five restores left a difference that configures nothing: `disable_routing.m1`
and `m2` an empty `system ip { }` and `firewall ipv4 forward filter { }` on the
firewall, `zone_policy_enforcement.m3` `interface eth3` listed last in the DMZ zone,
`wan_shaping_policy_repair.m1` and `m2` wan-edge's htb `direct_packets_stat` counter.
An episode redeploys the lab, so none is inherited.

The draws changed with the merge and are now campus_small's, method for method: one
firewall presents the fault compiler the same routers, services and desks on both
labs, and no fault lands on the switches medium adds. The two-firewall lab passed its own draws
(33 methods, `assured_bandwidth.m1` included, archived since 2026-10-27) on
2026-10-26 in 97 minutes; that table is in the git history.

| Method | | Affected | Degradation seen | Repair oracle after restore | Preservation |
|---|---|---|---|---|---|
| `disable_interface.m1` | router_interface_down | srv-site | yes | yes | yes |
| `disable_interface.m2` | service_interface_down | web-dmz | yes | yes | yes |
| `disable_interface.m3` | service_low_mtu | srv-site | yes | yes | yes |
| `disable_routing.m1` | router_ipv4_forwarding_disabled | external1, host-access-1, host-access-2, host-access-3, host-access-4, sftp-dmz, web-dmz | yes | yes | yes |
| `disable_routing.m2` | router_forwarded_traffic_dropped | external1, host-access-1, host-access-2, host-access-3, host-access-4, sftp-dmz, web-dmz | yes | yes | yes |
| `disable_routing.m3` | service_route_lookup_prohibited | sftp-dmz | yes | yes | yes |
| `disable_routing.m4` | service_default_route_removed | web-dmz | yes | yes | yes |
| `drop_traffic_to_from_subnet.m1` | router_drops_traffic_from_subnet | web-dmz | yes | yes | yes |
| `drop_traffic_to_from_subnet.m2` | router_drops_traffic_to_subnet | web-dmz | yes | yes | yes |
| `drop_traffic_to_from_subnet.m3` | router_drops_traffic_to_from_subnet | web-dmz | yes | yes | yes |
| `drop_traffic_to_from_subnet.m4` | router_drops_icmp_to_from_subnet | web-dmz | yes | yes | yes |
| `remove_ip.m1` | router_interface_address_removed | host-access-2 | yes | yes | yes |
| `remove_ip.m2` | service_interface_address_removed | web-dmz | yes | yes | yes |
| `remove_ip.m3` | service_wrong_ip | sftp-dmz | yes | yes | yes |
| `remove_ip.m4` | service_wrong_prefix_length | sftp-dmz | yes | yes | yes |
| `remove_ip.m5` | service_duplicate_ip | web-dmz | yes | yes | yes |
| `wrong_routing_table.m1` | router_route_swapped_to_wrong_egress | srv-site | yes | yes | yes |
| `wrong_routing_table.m2` | router_more_specific_blackhole | srv-site | yes | yes | yes |
| `wrong_routing_table.m3` | router_route_via_unresolvable_next_hop | srv-site | yes | yes | yes |
| `wrong_routing_table.m4` | router_next_hop_loop | srv-site | yes | yes | yes |
| `wrong_routing_table.m5` | service_wrong_default_gateway | sftp-dmz | yes | yes | yes |
| `dhcp_provisioning.m1` | pool_subnet_unserved | host-access-4 | yes | yes | yes |
| `dhcp_provisioning.m2` | pool_gateway_misassigned | host-access-3 | yes | yes | yes |
| `dhcp_provisioning.m3` | pool_resolver_unadvertised | host-access-3 | yes | yes | yes |
| `dhcp_provisioning.m4` | pool_resolver_misassigned | host-access-2 | yes | yes | yes |
| `zone_policy_enforcement.m1` | zone_pair_ruleset_detached | host-access-1, host-access-4 | yes | yes | yes |
| `zone_policy_enforcement.m2` | zone_pair_shadowed_by_deny | host-access-1, host-access-2, host-access-3, host-access-4 | yes | yes | yes |
| `zone_policy_enforcement.m3` | zone_interface_unassigned | web-dmz | yes | yes | yes |
| `wan_shaping_policy_repair.m1` | shaping_policy_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m2` | assured_class_starved | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m3` | assured_classifier_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m4` | class_allocation_inverted | host-access-1 | yes | yes | yes |

Three defects the 2026-10-26 run (two firewalls) was the first to hit, fixed before it:

- **`dhcp_provisioning` could not pass its healthy oracle.** It resolved `web1`,
  a name only sme01-dns holds; the name is now the leased desk's own, in the
  zone of its resolver (`host-access-3.campus.lab`), and the resolver faults
  (m3, m4) show only there, every ICMP path staying up.
- **`drop_traffic_to_from_subnet` drew subnets a stateful firewall does not cut.**
  The firewall lets back the replies to sessions a subnet opened, so with
  one-way requirements a drop toward CORP (which only opens sessions) cut
  nothing. The family's shared subnet is now one that both opens and receives a
  required session.
- **`disable_interface.m1` / `remove_ip.m1` drew interfaces with no endpoint
  beside them** (the transit, `eth4`, the resolvers' segments): an empty blast
  radius, and the episode stopped before the subject was called. Such a draw is
  folded onto an observable interface.

## Known gaps

**It cannot run beside sme01-dns.** The firewall loads the conntrack helpers
(`set system conntrack modules ftp nfs sip tftp`), kernel modules shared with the
host. sme01-dns's firewall does not, and committing its NAT tries to unload them:
the commit fails while a campus lab holds them. The campus labs run beside each
other.

**Only the ICMP half of the policy is asserted.** The isolation requirements
are the zone-model denies (UNTRUST and DMZ toward TRUST, UNTRUST toward DMZ).
The intra-LAN isolation is not listed: the original's `TRUST-INTRA` rule 40
accepted every ICMP packet, so GUESTS pinged CORP whatever the matrix said about
sessions. The ping rules now follow the matrix, so it could be. The
`TRUST-INTRA` matrix itself is checked by nothing the oracle runs.

**`TRUST → TRUST` is not a fault target.** It is an intra-zone ruleset, not a
zone pair, and the zone-policy faults address pairs; declaring it would render
a `from TRUST` the zone never had.

**No security scenario but `cleartext_http_disclosure` binds.** The original
has no attacker port and no administration station; an `attacker1` cabled to an
access switch would make the same-L2 and VLAN-bypass scenarios applicable.

**The LAN shapers are not what the QoS scenarios exercise.** The firewall's
`QOS-UPLINK` shapes `bond0` at 1 Gbit with nothing to contend for; the
`qos_policy` the QoS families bind to is the remote site's circuit.

**No fault lands on a switch.** None of the campaign's methods selects a switch
(the routed and filtering families bind to the firewall, the routers and the hosts), so the
switches are what every fault crosses, not what it breaks. A dual-homed bundle
makes one cable or one aggregate redundant; a fault on a switch port would need a
method that cuts the bundle as a whole -- both members, or the port-channel on
both peers -- to show a blast radius at all.
