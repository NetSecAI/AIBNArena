# SME01 FW ContainerLab Topology

Firewall variant of [`sme01-vlan`](../sme01-vlan/README.md), built as a
**back-to-back (dual) firewall DMZ** behind a redundant layer-2 fabric.

| | `sme01-vlan` | `sme01-fw` |
|---|---|---|
| Access | layer-2 leaves | layer-2 leaves |
| Aggregation | two routed spines (IRB) | one **layer-2** aggregation switch |
| Inter-VLAN gateway | spine IRBs | `fw-int` |
| Internal policy | none | `fw-int`, one zone per VLAN |
| DMZ / Internet | behind leaf2 | `fw-ext`, servers cabled directly |
| Service policy | none | `fw-ext`, on source address and port |
| NAT | none | `fw-ext` only: masquerade + published web service |
| Node kinds | `nokia_srlinux`, `linux` | plus `vyos` |

The executable descriptor is
`scenarios/topologies/sme_leaf_firewall_dmz.yaml`.

## Four layers, each with one job

```
     user1  guest1  admin1  attacker1              finance1
       |      |       |        |                      |
     ┌────────────── leaf1 ──────────┐            ┌── leaf2 ──┐     layer 2, access
     │                               │            │           │
   e1-1                            e1-10..57    e1-1        e1-30
     │                                            │
     └────────────── spine1 ──────────────────────┘                 layer 2,
                       │ e1-3                                       aggregation
                       │
                     eth1
                  fw-int                inter-VLAN gateway + internal policy
                       │ eth2
                       │  10.255.1.0/30  routed transit: the only way out
                       │ eth1
                    fw-ext              DMZ + OUTSIDE + service policy + NAT
                ┌──────┼──────┐
              eth2   eth3   eth4
               │      │      │
             web1   app1  external1
```

**`fw-int`** owns every internal VLAN — it answers the address the endpoints use
as default gateway — and decides what may talk to what inside the enterprise.
Everything leaving the estate is routed to `fw-ext` over the transit link.

**`fw-ext`** owns the DMZ and the Internet, applies the service-level policy in
front of them, and is the only node in the topology that translates addresses.

Neither can be bypassed: there is no path from an internal endpoint to a DMZ
server that does not cross both. Measured, from `user1`:

```
traceroute -I 10.10.40.10
 1  10.10.10.1     fw-int
 2  10.255.1.2     fw-ext
 3  10.10.40.10    web1
```

| Zone | Firewall | Interface | Subnet |
|---|---|---|---|
| USERS | fw-int | `eth1.10` | 10.10.10.0/24 |
| GUEST | fw-int | `eth1.20` | 10.10.20.0/24 |
| FINANCE | fw-int | `eth1.30` | 10.10.30.0/24 |
| MGMT | fw-int | `eth1.99` | 10.10.99.0/24 |
| UPLINK | fw-int | `eth2` | 10.255.1.0/30 |
| INTERNAL | fw-ext | `eth1` | 10.255.1.0/30 |
| DMZ | fw-ext | `eth2`, `eth3` | 10.10.40.0/24, 10.10.50.0/24 |
| OUTSIDE | fw-ext | `eth4` | 203.0.113.0/24 |

Endpoint configuration is carried over unchanged — same addresses, same
`default via <subnet>.1`. Only the node answering that gateway moved.

## The layer-2 fabric

One aggregation switch between the leaves and the internal firewall. Each leaf
carries one 802.1Q trunk up to it, and it trunks every VLAN on to `fw-int`.
No layer-2 loop exists, because a VLAN lives on exactly one leaf and terminates
on the firewall's `vif`; this is why no spanning-tree protocol is configured.

`spine1` holds no IP address. It is a pure bridge — a spine that routed would be
a way around `fw-int`, which is the one thing this topology must not allow.

**Every trunk is a single point of failure for what it carries**, and that is a
deliberate, informed choice rather than an omission.

### Why there is no second spine

A second one was built, measured, and removed. The short version: this platform
cannot make two aggregation switches act as one, so a second spine could only
ever be an inert standby.

- **SR Linux has no vPC-style MLAG.** Its multi-chassis LAG is an EVPN
  ethernet-segment, and the commit rejects it outright without a BGP-VPN
  instance:
  `Leafref destination '.system.network-instance.protocols.bgp-vpn.bgp-instance{.id==1}' does not exist`.
  Getting there means iBGP EVPN between the spines, a VXLAN tunnel interface,
  and an IP underlay — turning a pure layer-2 aggregation tier into an
  EVPN-VXLAN fabric, with ethernet segments on *both* sides (a LAG from each
  leaf to the pair, and one from `fw-int` to the pair).
- **Without MLAG, both trunks land on the same firewall**, so they cannot both
  carry the gateway addresses: that would be two interfaces of one box in one
  subnet. The standby copy therefore had to be `disable`d.
- **Measured, it earned nothing.** It carried no traffic in the healthy state,
  no fault could target it — it had to be kept out of the pool on purpose, or
  it declared three endpoints affected while cutting a standby path breaks
  nothing — and no connectivity requirement asserted it. It cost 94 commands
  of the healthy state and a full SR Linux container.
- Failover, when driven by hand, cost **~8–10 s** of packet loss in both
  directions across three runs each way. The cost was MAC-table relearning, not
  the configuration change: VyOS sends no gratuitous ARP when a `vif` comes up.

`sme01-vlan` faces the same platform limit and resolves it differently, which is
worth knowing before reaching for either: its two spines are separate boxes, so
each can hold its own IRB address (`.1` and `.2`) and **both uplinks stay
forwarding** — nothing is disabled, and the passive thing is the address, not
the path. That trick is unavailable here precisely because both trunks would
terminate on one firewall.

If a second spine is ever wanted back, the honest options are the EVPN fabric
above, RSTP (both links cabled, one blocked, automatic failover in seconds), or
splitting the VLANs across the pair so each is the other's standby for half the
estate.

## Least-privilege policy, split across the two firewalls

Every ruleset is `default-action drop` with a rule 10 accepting
`established`/`related`, then one accept rule per authorised flow. **A zone pair
with no ruleset is denied by the zone model itself**, which is what isolates the
VLANs without a single deny rule being written.

The division of labour is deliberate: **coarse egress at `fw-int`, fine-grained
service policy at `fw-ext`**. Filtering the same flow twice in the same terms
would make the second firewall decoration.

### `fw-int` — internal segmentation

| From | To | Allowed |
|---|---|---|
| USERS | UPLINK | tcp/80, tcp/443, tcp/5201, ICMP |
| GUEST | UPLINK | tcp/80, tcp/443, ICMP |
| FINANCE | UPLINK | tcp/443, tcp/5201, ICMP |
| MGMT | UPLINK, USERS, GUEST, FINANCE | any |
| UPLINK | USERS, GUEST, FINANCE | ICMP only (see below) |
| USERS ↔ GUEST ↔ FINANCE | | **nothing** — no ruleset, denied by the zone model |
| anything | MGMT | replies only |

### `fw-ext` — the DMZ and the Internet

Every internal subnet arrives on one interface, so per-host granularity comes
from the source address. That address survives intact because **`fw-int` routes
and does not translate**; translation happens once, on the way out.

| From | To | Allowed |
|---|---|---|
| user1 | DMZ | web1 tcp/80, app1 tcp/5201 |
| guest1 | DMZ | web1 tcp/80 |
| finance1 | DMZ | app1 tcp/5201 |
| admin1 | DMZ, OUTSIDE | any |
| INTERNAL | OUTSIDE | tcp/80, tcp/443, masqueraded |
| external1 | DMZ | web1 tcp/80 |
| DMZ | INTERNAL, OUTSIDE | replies only, plus ICMP |
| OUTSIDE | INTERNAL | **nothing** |

### Measured

Port-level filtering at `fw-ext`, with real listeners on every tested port:

```
user1     -> web1:80      OPEN      user1     -> web1:22      BLOCKED
user1     -> app1:5201    OPEN      user1     -> app1:80      BLOCKED
guest1    -> web1:80      OPEN      guest1    -> app1:5201    BLOCKED
finance1  -> app1:5201    OPEN      finance1  -> web1:80      BLOCKED
external1 -> web1:80      OPEN      external1 -> web1:22      BLOCKED
                                    external1 -> app1:5201    BLOCKED
```

Inter-VLAN isolation at `fw-int` — these never reach `fw-ext` at all:

```
guest1 -> finance1   BLOCKED        user1  -> finance1   BLOCKED
guest1 -> user1      BLOCKED        user1  -> admin1     BLOCKED
```

And the zone boundaries at `fw-ext`:

```
external1 -> user1      BLOCKED     OUTSIDE reaches no internal endpoint
external1 -> finance1   BLOCKED
external1 -> admin1     BLOCKED
web1      -> finance1   BLOCKED     a DMZ server reaches only what it must
web1      -> user1:4444 BLOCKED     ICMP is opened, sessions are not
```

### The two firewalls really do stack

Disabling one rule on `fw-int` blocks a flow `fw-ext` still permits:

```
fw-int FINANCE-TO-UPLINK rule 30 disabled   finance1 -> app1:5201   BLOCKED
rule re-enabled                             finance1 -> app1:5201   OPEN
```

### ICMP is opened on every declared flow

The benchmark oracle probes reachability with `ping`. Filtering on ports alone
would fail all ten connectivity checks while the services themselves stayed
reachable, so each authorised flow also carries an
`icmp type-name echo-request` rule. This is a concession to a ping-based oracle,
not a security choice, and it is the reason `UPLINK -> USERS` and
`DMZ -> INTERNAL` exist at all: the required flows are checked in both
directions, so a DMZ server must be able to ping inland even though it should
never initiate a session there — and, as the measurements above show, it cannot.

### NAT

Source NAT masquerades each internal subnet behind `fw-ext`'s own OUTSIDE
address. The DMZ is **not** translated: its servers are reached on their own
addresses, which is what the `external1 -> web1` requirement asserts.
Destination NAT publishes the web service on `203.0.113.1:80 -> 10.10.40.10:80`.

## The `vyos` node kind

`vyos` is the third node kind, alongside `linux` and `nokia_srlinux`. It touches
five adapter files: `state.py` (the `vyos_cli` command mode), `executor.py`
(`run_vyos_cli_batch`, `run_vyos_op`, `run_vyos_show_config`), `env.py`,
`injector.py`, `observer.py`, and `ani.py`.

**The ANI branch matters most**: without it `get_running_config` falls back to
`ip address show`, which would show the addresses and routes but not the zones
and policies — the very thing a firewall scenario is about.

### VyOS applies what parses and skips the rest

Unlike an SR Linux candidate, a VyOS batch is not atomic. An unusable command
prints `Set failed` / `is not valid` and **the shell still exits 0**, so a
malformed fault would report success and change nothing. `run_vyos_cli_batch`
scans the transcript for those markers and turns them into a failure; the
markers live in `executor.py` as `VYOS_FAILURE_MARKERS`.

### A default route on a lab node loses to containerlab

`fw-int` reaches the DMZ and the Internet through three explicit static
prefixes, not through `0.0.0.0/0`. Containerlab installs its own management
default via `eth0` as a **kernel** route, which wins in the FIB: a static
`0.0.0.0/0` is accepted, shows up in `show ip route` without the `>*` selected
marker, and forwards nothing. Every internal endpoint was unreachable on the
first run of this lab for exactly that reason.

## Files

- `topology.clab.yml`: containers, images, management network (`172.23.0.0/24`);
- `states/healthy.json`: 499 commands (21 `shell`, 107 `srl_cli`, 371 `vyos_cli`);
- `states/generate_healthy_state.py`: generator — edit the VLAN, zone and policy
  tables there, not the JSON.

## Deploy

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-fw/topology.clab.yml
```

Two VyOS nodes boot on systemd and take noticeably longer than SR Linux; allow
several minutes before applying state. The management network is `172.23.0.0/24`,
so this lab runs alongside `sme01-small`, `sme01-vlan` and `sme01-dns`.

## Apply and verify

```bash
./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/sme01-fw/states/healthy.json \
  --topology scenarios/topologies/sme_leaf_firewall_dmz.yaml \
  --verify --verify-timeout 150
```

Observed on a fresh deployment: `verify=True`, ten directed checks at 0% loss.

Useful manual checks:

```bash
docker exec clab-sme01-fw-fw-int /opt/vyatta/bin/vyatta-op-cmd-wrapper show interfaces
docker exec clab-sme01-fw-user1 traceroute -I -n 10.10.40.10   # both firewalls

docker exec -i clab-sme01-fw-fw-ext vbash -s <<'CMD'
source /opt/vyatta/etc/functions/script-template
configure
show firewall zone
exit
CMD
```

`traceroute` without `-I` shows only the first hop: its UDP probes are dropped
by the egress policy, which allows tcp/80, tcp/443, tcp/5201 and ICMP and
nothing else. That is the policy working, not a broken path.

**Never `docker restart` a lab node**: containerlab creates the veth pairs at
deploy time and a restart loses them, leaving the node with no data interface.
Redeploy instead.

## Validated fault methods

Every method the catalog can materialise against this topology was injected on
a freshly deployed lab, its blast radius compared with the declared expectation,
and restored: **20 faults, 19 recovered by their own restore commands**, one by
a healthy re-apply.

> **These numbers were measured on the two-spine build and have not been
> re-run since spine2 was removed.** The topology still compiles and
> materialises cleanly — 40 instances, 0 errors — but the blast radii below,
> particularly `fw_disable_interface.m2`, need re-measuring once the scenario
> catalogue is settled.

| Family | Methods | Target | Failing checks |
|---|---|---|---|
| `fw_disable_interface` | m1 | leaf1 access port | 2 |
| `fw_disable_interface` | m2 | spine1 trunk to fw-int | 8 |
| `fw_disable_interface` | m3 | fw-int `eth1.10` | 4 |
| `fw_disable_interface` | m4, m5 | external1 | 2, 2 |
| `fw_disable_routing` | m1–m4 | fw-ext, fw-int, user1, guest1 | 10, 8, 4, 2 |
| `fw_drop_traffic_to_from_subnet` | m1–m4 | fw-ext `eth3` | 4 each |
| `fw_remove_ip` | m1–m5 | fw-ext, web1, guest1, external1 | 8, 6, 2, 2, 6 |
| `qos.link_impairment` | m1, m2 | finance1 | 2, 1 |

`m2` of `fw_disable_interface` is the one that exercises the fabric: cutting a
trunk takes down every endpoint whose VLAN it carries, and there is no second
path to absorb it.

The subnet-guard family needed `default-action return` on its ruleset. With the
VyOS default it took down all ten checks instead of four: a jump rule with no
match sends every forwarded packet into the ruleset, where the default action
decides its fate. Collateral damage is part of the injection harness' pass
criterion, precisely because that failure looked like a success.

### One blast-radius finding, not fixed

**The transit link's blast radius is understated.**
`fw_remove_ip.m1` removes `10.255.1.2` from `fw-ext`'s transit interface and
declares `affected_nodes: []`, because `segment_hosts()` counts only evaluated
endpoints and `fw_transit` holds none. Measured: **8 of 10 checks fail.** The
blast radius is computed from segment membership rather than from the
connectivity requirements a fault actually breaks, and an infrastructure segment
between two routers falls straight through it. The same flaw shows on
`sme01-dns` for the nameserver segments; adding a transit link gives it a second
instance here.

## Known gaps

**The deny rules are the security properties this topology exists to enforce,
and nothing in the benchmark verifies them.** `connectivity_requirements` can
only say "must reach"; there is no way to express "must not reach", so the
healthy state configures inter-VLAN isolation, the OUTSIDE deny and the
per-port restrictions, and the oracle checks none of them. They were verified by
hand here, and a regression would pass unnoticed. Two consequences: a scenario
that *relaxes* the policy to repair connectivity would score as a success, and
the ICMP allowances above exist purely because the oracle pings.

**Neither firewall protects itself.** No `local` zone is defined, so the zone
policy governs forwarded traffic only and each firewall's own management
services stay reachable from the zones it faces.

**The fabric has no redundancy, by decision.** `spine1` and each of the three
trunks are single points of failure. A second spine was built and removed
because this platform cannot make two aggregation switches act as one without
an EVPN fabric — the reasoning and the measurements are above. Anyone reading
this lab as a template for a production design should read that section first.

This is item 4 of `notes.txt`. The hardened policy sharpens the need: there is
now a real security posture to protect, and no way to assert it.

## Destroy

```bash
containerlab destroy -t benchmarks/testbeds/containerlab/sme01-fw/topology.clab.yml --cleanup
```
