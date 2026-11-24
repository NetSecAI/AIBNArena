# campus_small ContainerLab Topology

[`campus_medium`](../campus_medium/README.md) folded down: two layer-2 Arista cEOS
access switches trunked straight to a single VyOS firewall, which holds the LAN
(`TRUST`), the DMZ and the Internet (`UNTRUST`) zones. The endpoints, the two
resolvers, the VLAN and address plan, the DHCP reservations and every ruleset but
`TRUST-INTRA` are medium's, and so the original lab's. This lab had
one firewall first; medium and large merged their two into one like it on
2026-10-28, on the same ports for everything but the LAN side.

The executable descriptor is `scenarios/topologies/campus_small.yaml`.

| | `campus_medium` | `campus_small` |
|---|---|---|
| Switches | 4 access + 2 aggregates (MLAG pair), cEOS | 2 access, cEOS, no aggregate and so no MLAG |
| Trunks | LACP bundles over the MLAG pair, `bond0` on the firewall | one plain port per access switch: `Ethernet1` to `eth1`/`eth2` |
| Firewall | `firewall` (two merged on 2026-10-28) | `firewall` |
| LAN gateway | `firewall` `bond0` vifs (`eth1` + `eth2`) | `firewall` `eth1`/`eth2` vifs |
| DMZ, Internet | `firewall` `eth3`–`eth6` | the same |
| Zones | `TRUST`, `DMZ`, `UNTRUST`, `SITE` (+ `LOCAL`, `MGMT`) | the same |
| `TRUST-INTRA` | least privilege since 2026-10-28: named services | the original's |
| Management subnet | `172.27.0.0/24` | `172.28.0.0/24` |
| Remote site | `firewall` `eth7` | the same |

## Layout

```
                     external1 192.168.122.1                 (the Internet)
                           │ eth6 192.168.122.202
  web-dmz  ── eth3 ┐       │
  sftp-dmz ── eth4 ├──── firewall ── eth7 ── router-wan ── wan-edge ══10 Mbit══ srv-site
                           (every zone, NAT, DHCP, QoS)
  dns-dmz  ── eth5 ┘   eth1 │      │ eth2
            VLANs 100,110  │      │  VLANs 120,130,140
                 switch-access-1  switch-access-2           layer 2
                   Et3│  Et4│      Et3│  Et4│  Et5│
           host-access-1 host-access-2  host-access-3 host-access-4 dns-access-4
```

| VLAN | Group | Gateway (`firewall`) | Access switch, port | Node | Address |
|---|---|---|---|---|---|
| 100 | CORP | `eth1.100` 10.0.0.1 | switch-access-1 `Ethernet3` | host-access-1 | 10.0.0.150, DHCP |
| 110 | VOIP | `eth1.110` 10.0.1.1 | switch-access-1 `Ethernet4` | host-access-2 | 10.0.1.150, DHCP |
| 120 | BMS | `eth2.120` 10.0.2.1 | switch-access-2 `Ethernet3` | host-access-3 | 10.0.2.150, DHCP |
| 130 | INFRA | `eth2.130` 10.0.3.1 | switch-access-2 `Ethernet5` | dns-access-4 | 10.0.3.53, static |
| 140 | GUESTS | `eth2.140` 10.0.4.1 | switch-access-2 `Ethernet4` | host-access-4 | 10.0.4.150, DHCP |

The DMZ and Internet segments keep medium's addresses: web-dmz 172.16.2.10,
sftp-dmz 172.16.1.10, dns-dmz 172.16.3.53, external1 192.168.122.1, each behind
the firewall's `.254` (or `.202` on the Internet side). All four are static: only
the desks lease, since 2026-11-06 (see
[medium's README](../campus_medium/README.md#what-changed-from-the-original-and-why)).

## What one firewall changes

What merging the original's two firewalls changed is in
[medium's README](../campus_medium/README.md#one-firewall), and holds here: no
transit, `SRV-DNS` for the LAN resolver and `SRV-DNS-DMZ` for the DMZ one,
firewall-interne's `TRUST-LOCAL`. What is this lab's own:

- **`TRUST-INTRA` is the original's**: CORP opens anything toward BMS and VOIP,
  every group pings every other (rule 40). Medium's names its services since
  2026-10-28 (see its README).
- **The shaper** egresses the two access trunks: `QOS-UPLINK-ACC1` on `eth1`
  (CORP, VOIP), `QOS-UPLINK-ACC2` on `eth2` (BMS, GUESTS), each class as the
  original defines it.
- **No blackhole for `10.0.0.0/16`** since 2026-10-28. A destination inside the
  aggregate but in no connected `/24` leaves by the default route and dies at
  external1, which does not forward: with one firewall the discard route guarded
  no loop, and the agents took it for the fault (8 episodes of E19 changed it).
- **`DMZ-INTRA`.** The three DMZ segments are one zone, whose traffic VyOS
  permits by default. Here, as on medium since 2026-10-28, the DMZ zone's
  intra-zone filtering denies by default and lets through the DMZ resolver
  (`SRV-DNS-DMZ`, udp and tcp 53) and ping: the HLD's DMZ-to-DMZ default deny,
  which the formal verifier checks (`HLD_DZF_INTER_DMZ_ENFORCEMENT`,
  `DHCP_DNS_UNREACHABLE`).

Everything medium's README says about the images, DHCP, DNS, the Internet node
and the management port applies unchanged.

## The remote site

Medium's, on the same port (`eth7`) and at the same addresses: router-wan, wan-edge with the 10 Mbit circuit on its `eth2`, srv-site with
the iperf3 sinks ([`campus_site.py`](../../campus_site.py), and
[medium's README](../campus_medium/README.md#the-remote-site) for what each part is
for). The firewall routes `10.1.0.0/24` explicitly through router-wan; its default
goes to external1, which drops the site's traffic once that route is unusable. The
zone is `SITE`, with `TRUST-SITE` and `SITE-LOCAL` as on medium. `healthy_greenfield.json`
keeps the circuit and drops the classes, for `assured_bandwidth`
(`benchmarks/configs/testbeds/campus_small-greenfield.toml`).

## Files

- `topology.clab.yml`: containers, links, pinned MACs, management network `172.28.0.0/24`;
- `states/generate_healthy_state.py`: generator — edit its tables, not the JSON. Routes,
  the shaping policy and the iperf3 sinks are read from the descriptor;
- `states/healthy.json`: 415 commands (65 `shell`, 45 `eos_cli`, 305 `vyos_cli`); the
  switches' lines are rendered from the descriptor by
  [`benchmarks/testbeds/campus_fabric.py`](../../campus_fabric.py);
- `states/healthy_greenfield.json`: the same without wan-edge's class policy (413 commands);
- `ceos/startup.cfg`: the switches' startup configuration, which only disables Zero Touch Provisioning;
- `dhcp/udhcpc.script`, `dns/access/`, `dns/dmz/`: as in `campus_medium`.

## Deploy, apply and verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/campus_small/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/campus_small/states/healthy.json \
  --topology scenarios/topologies/campus_small.yaml \
  --verify --verify-timeout 180
```

These figures, and those under Verified, were measured on the SR Linux port the
switches replaced; the firewall and its policy are unchanged. On a fresh deployment through the Judge's own path
(`ContainerlabPlatform.deploy_or_reset` on `benchmarks/configs/testbeds/campus_small.toml`,
then `apply_reference_state`, 2026-10-23): deploy 107 s, reference state applied
and converged in 140 s, 33 command groups, none failed, the 11 address checks
and 5 name checks at 0 % loss. It runs alongside `campus_medium`.

## Verified

- **Configuration**: all 279 VyOS commands committed; each of the eight rulesets
  identical to medium's (with `SRV-DNS-DMZ` for the DMZ resolver).
- **`DMZ-INTRA`** (2026-10-28, cEOS lab): applied on a running lab, the 21 healthy
  checks at 0 % loss; web-dmz and sftp-dmz resolve `dmz.lab` through dns-dmz and
  ping across segments, sftp-dmz -> web-dmz tcp/80 and web-dmz -> dns-dmz tcp/80
  time out, host-access-1 -> web-dmz tcp/80 still answers 200. The fault methods
  were replayed with it on 2026-10-28 (see [Validated fault methods](#validated-fault-methods)).
- **Behaviour**: the same 67 probes as medium, with real listeners, all as the
  original policy says — `TRUST-INTRA` over the 12 pairs of user groups in TCP and
  ICMP, `TRUST-DMZ`, `TRUST-UNTRUST`, `DMZ-UNTRUST` port by port, the zone-model
  denies, `TRUST-LOCAL`/`DMZ-LOCAL`, the blackhole (removed since), and the masquerade (LAN and DMZ
  sources seen by `external1` as `192.168.122.202`).

## Validated fault methods

Every method of the four campaign domains, live on one deployment of the cEOS lab
(2026-10-26, the experiment files' seeds), through the Judge's own pieces without a
redeploy between methods: the healthy oracle, the fault injected and the
expected-degradation oracle evaluated with the lifecycle's re-injection loop, the
fault's restore replayed and convergence awaited, then the repair and preservation
oracles. 33 of 33 passed, in 96 minutes (deploy 62 s, reference state applied and
verified in 197 s).

**Replayed on 2026-10-28** (pistes_e20 B15), at `3b31400e` with the blackhole still
in place: after `DMZ-INTRA`, LLDP on the trunks (`1fdea59b`) and the zone-apply and
QoS-contention fixes (`0fc9551f`). The 32 methods of the campaign domains, same
procedure, one deployment (deploy 63 s, reference state applied and verified in
196 s), 100 minutes: 32 of 32 passed every oracle, each fault seen on its first
injection. The running configuration of every device was also compared with the
reference after each restore. Four restores left a difference that configures
nothing: `disable_routing.m1` and `m2` an empty `system ip { }` and `firewall ipv4
forward filter { }` on the firewall, `zone_policy_enforcement.m3` `interface eth3`
listed last in the DMZ zone, `wan_shaping_policy_repair.m1` wan-edge's htb
`direct_packets_stat` counter. An episode redeploys the lab, so none is inherited.
The draws below did not change. The blackhole's removal came after the replay;
no fault binds to it, and no oracle probes an address it covered.

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
| `assured_bandwidth.m1` (archived 2026-10-27, see `scenarios/archive/README.md`) | sustained_udp_cross_traffic | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m1` | shaping_policy_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m2` | assured_class_starved | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m3` | assured_classifier_removed | host-access-1 | yes | yes | yes |
| `wan_shaping_policy_repair.m4` | class_allocation_inverted | host-access-1 | yes | yes | yes |

## Known gaps

As in `campus_medium`: only the ICMP half of the policy is asserted,
`TRUST → TRUST` is no fault target, no security scenario but
`cleartext_http_disclosure` binds, and every trunk is a single point of failure.
