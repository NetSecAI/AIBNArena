# SME01 VLAN ContainerLab Topology

Layer-2 variant of [`sme01-small`](../sme01-small/README.md). Same cabling, same
addressing, same ten connectivity checks — but the forwarding model is inverted:

| | `sme01-small` | `sme01-vlan` |
|---|---|---|
| Leaves | routers, one routed access interface per endpoint | **pure layer-2 switches**, one bridge domain per VLAN |
| Fabric links | routed `/31` point-to-point | **802.1Q trunks** |
| Endpoint gateway | the local leaf | **the spines** |
| Inter-VLAN routing | distributed on the leaves | **centralised on the spines** |
| Routing protocol | static routes + ECMP | none — every VLAN is directly connected on each spine |

The executable descriptor is
`scenarios/topologies/sme_leaf_spine_dmz_vlan.yaml`.

## Forwarding Model

Leaves run no IPv4 at all on the data plane. Each VLAN is a `mac-vrf`
network-instance joining the untagged access ports to both fabric uplinks, which
are 802.1Q trunks carrying only the VLANs local to that leaf.

Spines terminate every VLAN on an IRB subinterface placed in the `default`
ip-vrf. Because all seven VLAN prefixes are directly connected on each spine,
inter-VLAN routing needs no static route: `user1 -> web1` is a single L3 hop.

```
user1 --(untagged vlan10)-- leaf1 ==(trunk 10,20,99)== spine1  irb0.10 / irb0.40 ...
                                                          |
web1  --(untagged vlan40)-- leaf2 ==(trunk 30,40,50,60)===+
```

No layer-2 loop exists: a VLAN lives on exactly one leaf, so a frame reaching a
spine is terminated on its IRB and never bridged back toward the other leaf.
This is why no spanning-tree protocol is configured.

## VLAN And Address Plan

| VLAN | Segment | Subnet | spine1 IRB | spine2 IRB | Leaf | Access port | Endpoint |
|---|---|---|---|---|---|---|---|
| 10 | users | 10.10.10.0/24 | .1 | .2 | leaf1 | ethernet-1/10 | user1 .10 |
| 20 | guest | 10.10.20.0/24 | .1 | .2 | leaf1 | ethernet-1/20, ethernet-1/30 | guest1 .10, attacker1 |
| 30 | finance | 10.10.30.0/24 | .1 | .2 | leaf2 | ethernet-1/30 | finance1 .10 |
| 40 | dmz | 10.10.40.0/24 | .1 | .2 | leaf2 | ethernet-1/40 | web1 .10 |
| 50 | servers | 10.10.50.0/24 | .1 | .2 | leaf2 | ethernet-1/50 | app1 .10 |
| 60 | external | 203.0.113.0/24 | .1 | .2 | leaf2 | ethernet-1/58 | external1 .10 |
| 99 | management | 10.10.99.0/24 | .1 | .2 | leaf1 | ethernet-1/57 | admin1 .100 |

Endpoint configuration is unchanged from `sme01-small`: same addresses, same
`default via <subnet>.1`. Only the node answering that gateway address moved
from the leaf to spine1.

### Gateway Redundancy

SR Linux v26.3.2 implements no VRRP, and its anycast gateway is an EVPN feature:
configuring the same anycast address and virtual-router-id on both spines makes
the leaf see one MAC on two uplinks, and its MAC-move detection flags the entry
`duplicate`. This was reproduced on this lab before being discarded.

The lab therefore uses **distinct IRB addresses per spine**. spine1 holds `.1`,
the address endpoints use as default gateway; spine2 holds `.2` and is a fully
configured standby inter-VLAN router. Only spine1 carries the `gateway` role in
the descriptor: the compiler requires exactly one gateway per segment — every VLAN, every IRB, ready to forward.
Failover is not automatic: it is a routing decision on the endpoints, which
makes it a usable benchmark objective rather than hidden protocol behaviour.

Verified failover: with spine1's fabric links disabled and endpoints repointed
to `.2`, `user1 -> web1` keeps 0% loss and traceroute shows `10.10.10.2` as the
single L3 hop.

### attacker1

`attacker1` sits on `leaf1:ethernet-1/30` as an untagged access port in VLAN 20,
sharing the guest broadcast domain with `guest1`, and its data interface starts
down. Unlike the routed lab, an enabled attacker here is layer-2 adjacent to a
legitimate endpoint, which is what makes ARP-level scenarios expressible.

## Files

- `topology.clab.yml`: containers, images, management network (`172.22.0.0/24`), physical links;
- `states/healthy.json`: reviewed idempotent state, 338 commands (317 `srl_cli`, 21 `shell`);
- `states/generate_healthy_state.py`: generator for `healthy.json` — edit the VLAN table there, not the JSON.

## Deploy

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-vlan/topology.clab.yml
```

The management network is `172.22.0.0/24`, so this lab can run alongside
`sme01-small` (`172.21.0.0/24`).

## Apply And Verify

```bash
./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/sme01-vlan/states/healthy.json \
  --topology scenarios/topologies/sme_leaf_spine_dmz_vlan.yaml \
  --verify --verify-timeout 90
```

Observed on a fresh deployment: `verify=True`, ten directed checks at 0% loss,
and a full 7x7 endpoint ping matrix at 42/42 directed pairs with 0% loss.

Useful manual checks:

```bash
docker exec clab-sme01-vlan-leaf1 sr_cli -c "show network-instance vlan10 bridge-table mac-table all"
docker exec clab-sme01-vlan-spine1 sr_cli -c "show network-instance default route-table ipv4-unicast summary"
docker exec clab-sme01-vlan-user1 traceroute -n 10.10.40.10   # 10.10.10.1 then 10.10.40.10
```

The leaves hold no in-band IPv4 address; they are reachable only over the
out-of-band ContainerLab management network, which is what `docker exec` uses.

## Benchmark Integration

The connectivity domain is wired up. `scenarios/topology_applicability.json`
maps each scenario file to the topologies it can be compiled against, and
`compile_scenarios()` honours it, so this topology compiles only the scenarios that
can actually be injected and judged:

```text
sme_leaf_spine_dmz_small   24 tasks   disable_interface, disable_routing,
                                      drop_traffic_to_from_subnet, remove_ip,
                                      routing_setup, wrong_routing_table
sme_leaf_spine_dmz_vlan    14 tasks   vlan_disable_interface, vlan_disable_routing,
                                      vlan_remove_ip, vlan_bridge_domain_misconfiguration
```

### Switched scenario families

`vlan_disable_interface` carries over `disable_interface`, with the
router methods replaced by switch methods; the endpoint methods are unchanged.
`vlan_remove_ip`, `vlan_disable_routing` and `vlan_drop_traffic_to_from_subnet`
carry over their routed counterparts, with the router-scoped methods rebound to
`{node: gateway}`. `vlan_bridge_domain_misconfiguration` is new and takes the
place of `wrong_routing_table`: the switched equivalent of a wrong routing table
is a port in the wrong bridge domain.

All twelve SR Linux methods were injected on a fresh deployment of this lab,
then restored, and each produced exactly its expected blast radius:

| Method | Expected affected | Failing checks | After restore |
|---|---|---|---|
| `switch_access_port_down` | `web1` | 6 | healthy |
| `switch_trunk_port_down` | `app1 external1 finance1 web1` | 10 | healthy |
| `access_port_removed_from_bridge_domain` | `web1` | 6 | healthy |
| `access_port_moved_to_wrong_vlan` | `web1` | 6 | healthy |
| `trunk_vlan_pruned` | `external1` | 2 | healthy |
| `inter_vlan_router_forwarding_disabled` | all six endpoints | 10 | healthy |
| `gateway_irb_address_removed` | `finance1` | 2 | healthy |
| `inter_vlan_forwarded_traffic_dropped` | all six endpoints | 10 | healthy |
| `gateway_drops_traffic_from_subnet` | `app1` | 4 | healthy |
| `gateway_drops_traffic_to_subnet` | `app1` | 4 | healthy |
| `gateway_drops_traffic_to_from_subnet` | `app1` | 4 | healthy |
| `gateway_drops_icmp_to_from_subnet` | `app1` | 4 | healthy |

### Why gateway, not router

The routed families select `{node: router}`, which resolves to both spines.
spine2 is the standby: cutting it changes nothing, so the compiled blast radius
would never materialise and the scenario would be judged as failed for a reason
that has nothing to do with the agent. The switched families select
`{node: gateway}`, which is spine1 alone — the router that actually forwards.

### What the switch role required

Selectors resolve `{node: router}` through `nodes_with_role`, so the leaves —
which hold no `router` role — were unreachable. The leaves now carry a `switch`
role, and the compiler understands `{node: switch}` with `interface: access` or
`interface: trunk`. Two details make the blast radius exact rather than merely
plausible:

- access ports declare `attached_endpoint`, because a bridged port cuts the node
  cabled to it, not every host of its VLAN — without it, disabling `attacker1`'s
  port would have been scored as cutting `guest1`;
- trunk selection keeps only the uplinks whose peer holds the `gateway` role.
  The uplink toward the standby spine carries no traffic, so cutting it compiles
  to an empty and untestable expectation.

Blast radii are intersected with `endpoint_nodes()`, which keeps `admin1` out of
expectations no connectivity check covers.

### Still open

The blast-radius oracle (layer 3 of the original analysis) is unchanged. It
derives the expected affected set from `segment_hosts`, which answers "which
hosts live on this interface's segment" rather than "which flows traverse this
element". The switched families above avoid it by computing their own expected
set, and by binding to the active gateway so the question does not arise. The
generic path is still wrong for fabric links on routed topologies, where cutting
one compiles to an empty expectation — and an empty expectation disables judging
entirely in `_connectivity_impact`, so the scenario passes silently.

The QoS and security domains are mapped in the applicability file but no
switched variants exist for them yet.

## Destroy

```bash
containerlab destroy -t benchmarks/testbeds/containerlab/sme01-vlan/topology.clab.yml --cleanup
```
