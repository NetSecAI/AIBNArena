# campus_large ContainerLab Topology

[`campus_medium`](../campus_medium/README.md) grown by a third building and two
business VLANs. Everything medium holds keeps its name, port and address, so
medium is a subset of this lab: the firewall, its policy, the DHCP service, the
resolvers, the DMZ, the Internet node and the remote site are medium's, and
everything medium's README says about the port from the original lab applies --
the merge of the original's two firewalls into one (2026-10-28) included.

The executable descriptor is `scenarios/topologies/campus_large.yaml`.

| | `campus_small` | `campus_medium` | `campus_large` |
|---|---|---|---|
| Nodes | 15 | 19 | 29 |
| Arista cEOS switches | 2 access | 4 access + 2 aggregates | 8 access + 3 aggregates |
| MLAG | none | the two aggregates | aggregates 1 and 2 (buildings A, B); building C's stands alone |
| VyOS | firewall, router-wan | firewall, router-wan | firewall, router-wan |
| LAN VLANs / DHCP desks | 5 / 4 | 5 / 4 | 10 / 8 |
| Required flows | 13 | 13 | 20 |
| Management subnet | `172.28.0.0/24` | `172.27.0.0/24` | `172.29.0.0/24` |

## Layout

```
                     external1 192.168.122.1                            (the Internet)
                           │ eth6
  web-dmz  ── eth3 ┐       │
  sftp-dmz ── eth4 ├──── firewall ── eth7 ── router-wan ── wan-edge ══10 Mbit══ srv-site
  dns-dmz  ── eth5 ┘       │   (every zone, NAT, DHCP, QoS)
        bond0 = eth1 + eth2 ┌─┴───────────────────────┐ eth8              gateway of every VLAN
                     Et1 ┌──┴──┐ Et1                  │ Et1
       switch-aggregate-1 ═══ switch-aggregate-2   switch-aggregate-3      buildings A+B (MLAG), C
          Po999 = Et8 + Et9, MLAG CAMPUS_MLAG              Et2│     Et3│
         Et2..Et7 on each, Po2..Po7, MLAG 2..7            │        │
        acc-1  acc-2  acc-3  acc-4  acc-5  acc-6        acc-7     acc-8
         100    110    120  130/140  150    160        200/210     240
```

Every access switch of buildings A and B bundles `Ethernet1` (to aggregate-1) and
`Ethernet2` (to aggregate-2) into `Port-Channel1`, which the pair terminates as
MLAG N+1 on `Ethernet(N+1)`; building C's two switches each have one plain trunk,
`Ethernet1`, to aggregate-3's `Ethernet2` and `Ethernet3`. Every desk is on its
switch's `Ethernet3` (phone-access-7 on `Ethernet4`, dns-access-4 on switch-access-4's
`Ethernet4`).

| VLAN | Group | Subnet | Gateway | Access switch | Node | Address |
|---|---|---|---|---|---|---|
| 100 | CORP | 10.0.0.0/24 | `bond0.100` | switch-access-1 | host-access-1 | 10.0.0.150, DHCP |
| 110 | VOIP | 10.0.1.0/24 | `bond0.110` | switch-access-2 | host-access-2 | 10.0.1.150, DHCP |
| 120 | BMS | 10.0.2.0/24 | `bond0.120` | switch-access-3 | host-access-3 | 10.0.2.150, DHCP |
| 130 | INFRA | 10.0.3.0/24 | `bond0.130` | switch-access-4 | dns-access-4 | 10.0.3.53, static |
| 140 | GUESTS | 10.0.4.0/24 | `bond0.140` | switch-access-4 | host-access-4 | 10.0.4.150, DHCP |
| **150** | **FINANCE** | 10.0.5.0/24 | `bond0.150` | switch-access-5 | host-access-5 | 10.0.5.150, DHCP |
| **160** | **SERVERS** | 10.0.6.0/24 | `bond0.160` | switch-access-6 | app-access-6 | 10.0.6.10, static, nginx |
| **200** | CORP | 10.0.10.0/24 | `eth8.200` | switch-access-7 | host-access-7 | 10.0.10.150, DHCP |
| **210** | VOIP | 10.0.11.0/24 | `eth8.210` | switch-access-7 | phone-access-7 | 10.0.11.150, DHCP |
| **240** | GUESTS | 10.0.14.0/24 | `eth8.240` | switch-access-8 | host-access-8 | 10.0.14.150, DHCP |

VLAN 1X0 is 10.0.X.0/24 as on medium, the 2X0 series 10.0.1X.0/24, and each
pinned MAC still spells its reserved address (`02:00:0a:00:0e:96` is 10.0.14.150).
Building C's trunk is the firewall's `eth8` because `eth3` to `eth7` are the DMZ, the
Internet and the remote site on medium.

## What growing the campus changes

**A group spans buildings through several VLANs, not one stretched VLAN.** A DHCP
reservation pool serves one desk: the DHCP faults bind to a pool and expect one
affected endpoint, so a VLAN holding two leased desks would make a pool fault cut
two while it claims one. One VLAN per access switch satisfies it, and is campus
practice anyway; folding the MLAG pair into one switch, every VLAN is still a tree
of trunks (`test_campus_testbeds.py` asserts it).

**The policy names groups, and the groups gain members.** `CORP` is
`10.0.0.0/24` and `10.0.10.0/24`, `VOIP` and `GUESTS` likewise, so every rule the
original writes about a group reaches building C unchanged. What is new is what a
group in two VLANs needs and what the two business VLANs are for:

| `TRUST-INTRA` rule | |
|---|---|
| 10–12, 20, 21, 30, 35, 40 | medium's since 2026-10-28: CORP → BMS on HTTPS, BACnet/IP and ping, CORP → VOIP on SIP and ping, every group → the resolver on DNS and ping (the original's 10, 20 and 40 let CORP open anything and every group ping every other) |
| 15 `ALLOW-CORP-to-CORP-ping` | CORP is routed between its two VLANs, so its own traffic now meets the ruleset; ping only since 2026-10-28 (any protocol before) |
| 25, 27 `ALLOW-VOIP-to-VOIP-sip`, `-ping` | the same for VOIP: SIP and ping since 2026-10-28 |
| 22, 23 `ALLOW-CORP-to-SERVERS-http`, `-ping` | CORP reaches the application server (nginx): HTTP and ping since 2026-10-28 |
| 24, 26 `ALLOW-FINANCE-to-SERVERS-http`, `-ping` | FINANCE reaches it too, and nothing opens a session toward FINANCE |

**A second LAN shaper.** `QOS-UPLINK`, the original's, shapes `bond0`;
`QOS-UPLINK-AGG3` on `eth8` holds building C's CORP, VOIP and GUESTS classes, each
as the original defines its group's.

**Medium's least-privilege changes, carried over** (2026-10-28). Besides
`TRUST-INTRA` above, `DMZ-INTRA` filters inside the DMZ zone (default drop, the
DMZ resolver and ping allowed), and the firewall runs LLDP on its LAN ports
(`bond0`'s members and `eth8`), so the aggregates name it and those cables can be
read from LLDP.
Large's own rules (15, 22, 24, 25) name their services as well, in the table above.

**No discard route.** firewall-interne's `10.0.0.0/16` blackhole went with the
merge (see medium's README): with one firewall nothing bounces a destination inside
the `/16` back, and it dies at external1.

## Files

- `topology.clab.yml`: containers, links, pinned MACs, management network `172.29.0.0/24`;
- `states/generate_healthy_state.py`: medium's generator with the tables extended —
  edit them, not the JSON. Routes, the shaping policy and the iperf3 sinks are read
  from the descriptor, the remote site rendered by [`campus_site.py`](../../campus_site.py);
- `states/healthy.json`: 916 commands (82 `shell`, 401 `eos_cli`, 433 `vyos_cli`); the
  switches' lines are rendered from the descriptor by
  [`campus_fabric.py`](../../campus_fabric.py);
- `ceos/startup.cfg`: the switches' startup configuration, which only disables Zero Touch Provisioning;
- `states/healthy_greenfield.json`: the same without wan-edge's class policy, for
  `assured_bandwidth` (`benchmarks/configs/testbeds/campus_large-greenfield.toml`);
- `dhcp/udhcpc.script`, `dns/access/`, `dns/dmz/`: as in `campus_medium`.

## Deploy, apply and verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/campus_large/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/campus_large/states/healthy.json \
  --topology scenarios/topologies/campus_large.yaml \
  --verify --verify-timeout 300
```

On cEOS (2026-10-27, two firewalls), through the Judge's own path (`ContainerlabPlatform.deploy_or_reset`
on `benchmarks/configs/testbeds/campus_large.toml`, then `apply_reference_state`):
deploy 331 s, reference state applied and converged in 371 s. Eleven cEOS switches
take most of it; the platform allows 900 s for a deploy (`ContainerLabExecutor.
DEPLOY_TIMEOUT_SECONDS`), the 300 s it used to allow being what this lab first ran into.

## Validated fault methods

**Replayed on 2026-10-29 with one firewall** (pistes_e20 B15), after the merge and
the blackhole's removal. The 32 methods of the campaign domains, live on one
deployment of the cEOS lab (the experiment files' seeds), through the Judge's own
pieces without a redeploy between methods: the healthy oracle, the fault injected and
the expected-degradation oracle evaluated with the lifecycle's re-injection loop, the
fault's restore replayed and convergence awaited, then the repair and preservation
oracles. 32 of 32 passed every oracle, each fault seen on its first injection, in 158
minutes (deploy 299 s, reference state applied and verified in 349 s);
`wan_shaping_policy_repair.m3`, which needed a second run with two firewalls, passed
the first time. The running configuration of every device was also compared with the
reference after each restore. Four restores left a difference that configures
nothing: `disable_routing.m2` an empty `firewall ipv4 forward filter { }` on the
firewall, `zone_policy_enforcement.m3` `interface eth3` listed last in the DMZ zone,
`wan_shaping_policy_repair.m1` and `m2` wan-edge's htb `direct_packets_stat` counter.
An episode redeploys the lab, so none is inherited.

11 of the 32 draws changed with the merge: `disable_interface.m1`, `disable_routing`
m1 and m2, `drop_traffic_to_from_subnet` m1–m4, `remove_ip.m1` and
`zone_policy_enforcement` m1–m3, the methods that draw a router or a zone, now from
one firewall and wan-edge. The two-firewall lab passed its own draws (33 methods,
`assured_bandwidth.m1` included, archived since 2026-10-27) on 2026-10-26 and 27;
that table is in the git history.

| Method | | Affected | Degradation seen | Repair oracle after restore | Preservation |
|---|---|---|---|---|---|
| `disable_interface.m1` | router_interface_down | host-access-2 | yes | yes | yes |
| `disable_interface.m2` | service_interface_down | sftp-dmz | yes | yes | yes |
| `disable_interface.m3` | service_low_mtu | sftp-dmz | yes | yes | yes |
| `disable_routing.m1` | router_ipv4_forwarding_disabled | srv-site | yes | yes | yes |
| `disable_routing.m2` | router_forwarded_traffic_dropped | app-access-6, external1, host-access-1, host-access-2, host-access-3, host-access-4, host-access-5, host-access-7, host-access-8, phone-access-7, sftp-dmz, web-dmz | yes | yes | yes |
| `disable_routing.m3` | service_route_lookup_prohibited | srv-site | yes | yes | yes |
| `disable_routing.m4` | service_default_route_removed | srv-site | yes | yes | yes |
| `drop_traffic_to_from_subnet.m1` | router_drops_traffic_from_subnet | host-access-2 | yes | yes | yes |
| `drop_traffic_to_from_subnet.m2` | router_drops_traffic_to_subnet | host-access-2 | yes | yes | yes |
| `drop_traffic_to_from_subnet.m3` | router_drops_traffic_to_from_subnet | host-access-2 | yes | yes | yes |
| `drop_traffic_to_from_subnet.m4` | router_drops_icmp_to_from_subnet | host-access-2 | yes | yes | yes |
| `remove_ip.m1` | router_interface_address_removed | srv-site | yes | yes | yes |
| `remove_ip.m2` | service_interface_address_removed | web-dmz | yes | yes | yes |
| `remove_ip.m3` | service_wrong_ip | sftp-dmz | yes | yes | yes |
| `remove_ip.m4` | service_wrong_prefix_length | sftp-dmz | yes | yes | yes |
| `remove_ip.m5` | service_duplicate_ip | sftp-dmz | yes | yes | yes |
| `wrong_routing_table.m1` | router_route_swapped_to_wrong_egress | srv-site | yes | yes | yes |
| `wrong_routing_table.m2` | router_more_specific_blackhole | srv-site | yes | yes | yes |
| `wrong_routing_table.m3` | router_route_via_unresolvable_next_hop | srv-site | yes | yes | yes |
| `wrong_routing_table.m4` | router_next_hop_loop | srv-site | yes | yes | yes |
| `wrong_routing_table.m5` | service_wrong_default_gateway | web-dmz | yes | yes | yes |
| `dhcp_provisioning.m1` | pool_subnet_unserved | phone-access-7 | yes | yes | yes |
| `dhcp_provisioning.m2` | pool_gateway_misassigned | host-access-7 | yes | yes | yes |
| `dhcp_provisioning.m3` | pool_resolver_unadvertised | host-access-5 | yes | yes | yes |
| `dhcp_provisioning.m4` | pool_resolver_misassigned | host-access-3 | yes | yes | yes |
| `zone_policy_enforcement.m1` | zone_pair_ruleset_detached | host-access-1, host-access-4, host-access-8 | yes | yes | yes |
| `zone_policy_enforcement.m2` | zone_pair_shadowed_by_deny | host-access-1, host-access-2, host-access-3, host-access-4, host-access-7 | yes | yes | yes |
| `zone_policy_enforcement.m3` | zone_interface_unassigned | web-dmz | yes | yes | yes |
| `wan_shaping_policy_repair.m1` | shaping_policy_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m2` | assured_class_starved | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m3` | assured_classifier_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m4` | class_allocation_inverted | host-access-1 | yes | yes | yes |

## Known gaps

campus_medium's, unchanged: only the ICMP half of the policy is asserted
(FINANCE's isolation is not listed as a ping that fails; since `TRUST-INTRA`'s
ping rules follow the matrix it could be), `TRUST → TRUST` is no fault target, no
security scenario but `cleartext_http_disclosure` binds, and building C's trunks
are single points of failure.
