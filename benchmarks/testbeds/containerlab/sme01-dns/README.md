# SME01 DNS ContainerLab Topology

DNS variant of [`sme01-small`](../sme01-small/README.md), **with a single VyOS
firewall in place of the two SR Linux spines**. The firewall keeps everything
the spine pair did — fabric transit and the DHCP service the leaves relay to —
and adds what a spine pair could not do: it is the policy enforcement point
between the INTERNAL, DMZ and OUTSIDE zones.

| | `sme01-small` | `sme01-dns` |
|---|---|---|
| Fabric core | two SR Linux spines | one VyOS firewall |
| Path redundancy | ECMP across both spines | none — single firewall by design |
| DHCP | none | on the firewall, relayed by both leaves |
| DMZ servers | behind leaf2 | cabled directly to the firewall |
| Filtering | none | least-privilege zone policy on source, destination and port |
| NAT | none | source masquerade + web service published |
| Name service | none | internal resolver + DMZ authoritative server |
| Node kinds | `nokia_srlinux`, `linux` | plus `vyos` |

The executable descriptor is
`scenarios/topologies/sme_leaf_spine_dmz_dns.yaml`.

## Forwarding and security model

**Access stays routed.** The leaves are unchanged in nature: every endpoint sits
alone in its own `/24` behind a routed leaf interface, and the leaf is its
default gateway. What changed is where the leaves point and what sits behind
them.

```
                    firewall  (VyOS, 3 zones, DHCP server)
    eth1 ┌────────────┼──────────┬──────┬──────┐ eth6
  (p2p)  │       eth2 │ (p2p)    │ eth3 │ eth4 │ eth5
       leaf1        leaf2      web1  dns-dmz  app1   external1
      /  |  \          |        └──── zone DMZ ────┘   zone OUTSIDE
 user1 guest1 admin1  finance1
 dns-int attacker1
 └────── zone INTERNAL (via the leaves) ──────┘
```

| Zone | Firewall interface | Subnet | Behind |
|---|---|---|---|
| INTERNAL | `eth1` | 10.255.0.0/31 | leaf1: user1, guest1, admin1, dns-int |
| INTERNAL | `eth2` | 10.255.0.2/31 | leaf2: finance1 |
| DMZ | `eth3` | 10.10.40.0/24 | web1 (direct) |
| DMZ | `eth4` | 10.10.41.0/24 | dns-dmz (direct) |
| DMZ | `eth5` | 10.10.50.0/24 | app1 (direct) |
| OUTSIDE | `eth6` | 203.0.113.0/24 | external1 (direct) |

Moving the DMZ servers off leaf2 onto the firewall is what makes the policy
meaningful: there is no longer a path between a DMZ application and an internal
endpoint that does not cross a zone boundary.

### Three zones, not one per subnet

`sme01-fw` gives every VLAN its own zone, because its leaves are layer-2 and
each VLAN arrives on the firewall as its own tagged subinterface. Here access is
routed, so both leaves aggregate every internal subnet onto a single
point-to-point link each, and **a zone cannot separate users from finance**.

That is not a limitation for what this topology exists to enforce. The boundary
is DMZ ↔ INTERNAL, and that one *is* a zone boundary. Per-endpoint granularity
comes from matching **source address, destination address and port** inside the
rulesets, which is strictly more precise than the zone pair would be.

Intra-INTERNAL traffic is not filtered: `finance1 -> dns-int` crosses the
firewall between two INTERNAL interfaces, and the VyOS zone model permits
intra-zone traffic. The internal subnets are one trust domain here.

### Least-privilege policy

Every ruleset is `default-action drop` with a rule 10 accepting
`established`/`related`, then one accept rule per authorised flow. **A zone pair
with no ruleset is denied by the zone model itself**, which is why the policy
contains no deny rule at all: `OUTSIDE -> INTERNAL` is a reply-only ruleset and
everything else that is absent is simply not permitted.

| From | To | Allowed |
|---|---|---|
| user1 | DMZ | web1 tcp/80, app1 tcp/5201 |
| guest1 | DMZ | web1 tcp/80 |
| finance1 | DMZ | app1 tcp/5201 |
| dns-int | DMZ | dns-dmz udp/53, tcp/53 — the split-horizon forward |
| admin1 | DMZ, OUTSIDE | any |
| INTERNAL | OUTSIDE | tcp/80, tcp/443, masqueraded |
| external1 | DMZ | web1 tcp/80, dns-dmz udp/53 + tcp/53 |
| web1, app1 | INTERNAL | dns-int udp/53 + tcp/53 only |
| DMZ | OUTSIDE | replies, plus web1 ICMP (see below) |
| OUTSIDE | INTERNAL | **nothing** — replies only |

Measured on a freshly deployed lab, with real listeners on every tested port:

```
user1     -> web1:80      OPEN      user1     -> web1:22      BLOCKED
user1     -> app1:5201    OPEN      user1     -> app1:80      BLOCKED
guest1    -> web1:80      OPEN      guest1    -> app1:5201    BLOCKED
finance1  -> app1:5201    OPEN      finance1  -> web1:80      BLOCKED
external1 -> web1:80      OPEN      external1 -> web1:22      BLOCKED
                                    external1 -> app1:5201    BLOCKED
```

And the zone boundaries themselves:

```
external1 -> user1        BLOCKED   OUTSIDE reaches no internal endpoint
external1 -> finance1     BLOCKED
external1 -> dns-int      BLOCKED   not even the resolver
external1 -> admin1       BLOCKED
web1      -> finance1     BLOCKED   a DMZ server reaches only what it must
web1      -> admin1       BLOCKED
app1      -> guest1       BLOCKED
web1      -> user1:4444   BLOCKED   ICMP is opened, sessions are not
```

### ICMP is opened on every declared flow

The benchmark oracle probes reachability with `ping`. Filtering on ports alone
would fail every connectivity check while the services themselves stayed
reachable, so each authorised flow also carries an
`icmp type-name echo-request` rule, scoped to the same source and destination.

This is a concession to a ping-based oracle, not a security choice, and it is
the reason `DMZ -> INTERNAL` carries ICMP at all: the required flows are checked
in both directions, so a DMZ server must be able to ping inland even though it
should never initiate a session there — and, as the last line above shows, it
cannot. A service-aware oracle would let both go away.

### NAT

Source NAT masquerades each internal subnet behind the firewall's own OUTSIDE
address on the way to the Internet. The DMZ is **not** translated: its servers
are reached on their own addresses, which is what the `external1 -> web1`
requirement asserts.

```
Pre-NAT      Post-NAT     Proto
10.10.10.10  203.0.113.1  icmp
```

Destination NAT publishes the web service on the public address:
`203.0.113.1:80 -> 10.10.40.10:80`, verified reachable from `external1`.

## DHCP

The four client endpoints — `user1`, `guest1`, `finance1`, `admin1` — carry no
address of their own. Everything they need arrives in the lease:

| Option | | |
|---|---|---|
| 1 / 54 | address + mask | the reservation, e.g. `10.10.10.10/24` |
| 3 | `default-router` | the leaf interface that serves that subnet |
| 6 | `name-server` | `10.10.60.10` — `dns-int` |
| 15 | `domain-name` | `sme01.internal` |
| 119 | `domain-search` | `sme01.internal sme01.example`, so short names
resolve in both namespaces |

The servers stay statically addressed: the two nameservers, `web1`, `app1`, and
`external1`, which sits outside the network entirely. That split is the point —
an SME hands out desks by DHCP and pins the machines other things point at.

### The firewall serves, both leaves relay

Access is routed, so a DHCP broadcast reaches nothing but its own leaf. Each
client-facing subinterface therefore carries `dhcp-relay` with the gateway as
`gi-address`, pointing at the firewall's end of that leaf's fabric link
(`10.255.0.0` from leaf1, `10.255.0.2` from leaf2). Those are directly
connected, so the relay needs no route of its own, and the reply comes back to
the `gi-address` over the static routes already in place.

**There is no second server.** The spine pair gave DHCP redundancy; a single
firewall does not, and that is the trade this topology makes — one enforcement
point instead of two forwarding paths.

### Reservations, not a pool

Kea allocates from a `static-mapping` keyed on the MAC, one per desk, with no
floating range. The connectivity oracle pings addresses taken from the
descriptor and the internal zone holds fixed A records, so a client has to come
back on the same address every time.

Which is why the four client MACs are **pinned in `topology.clab.yml`**.
Containerlab otherwise draws a fresh random MAC at every deployment, and every
reservation would miss.

### Kea refuses a relay-only server unless you tell it where to listen

Every client subnet is remote: it lives behind a leaf, one endpoint per `/24`,
and **none of them is on a broadcast interface of the firewall's own**. VyOS
checks exactly that before committing, and rejects the whole `service
dhcp-server` subtree with

```
None of the configured subnets have an appropriate primary IP address on any
broadcast interface configured, nor was there an explicit listen-address
configured for serving DHCP relay packets!
```

`listen-address` is the answer, and it must be the firewall's *own* end of each
fabric link — `10.255.0.0` and `10.255.0.2`. Pointing it at the leaves' ends
(`10.255.0.1`, `10.255.0.3`) fails the same verify with
`listen-address ... not configured on any interface`. Kea then selects the
subnet from the relay's `gi-address`.

Worth knowing when this fails: **a rejected component does not fail the whole
commit.** The interfaces, routes, zones and policy all applied while
`service dhcp-server` silently did not, and the lab came up with a complete
firewall and no DHCP at all. `show dhcp server leases` is the check that
matters.

### The stock BusyBox udhcpc script is wrong inside a container

Twice over. Its `routes()` adds the lease's gateway with `metric 200+ifindex`
and only deletes defaults already pointing out of the interface, so
containerlab's management default via `eth0` (metric 0) keeps winning. And its
`resolvconf()` writes a temp file and renames it over `/etc/resolv.conf`, which
is a bind mount in a container: the rename fails with `EBUSY` and option 6 is
dropped without a word. `dhcp/udhcpc.script` replaces both.

### Ordering

`apply_state()` runs **every shell command before the first SR Linux command,
and every VyOS command last**
([`benchmarks/platforms/containerlab/env.py`](../../../platforms/containerlab/env.py)),
so a one-shot DHCP client in the healthy state would always lose the race
against both the fabric and the server it needs. `udhcpc` is therefore started
with `-b`: it forks into the background and keeps retrying until the relays and
the firewall are up. That is also what a real client does, and it leaves a
daemon running to renew.

One consequence worth knowing before a DHCP fault family is written: with an
hour-long lease, a client will not notice a *corrected* server on its own. A
restore that changes what the server hands out has to force a renewal
(`kill -USR1` on the udhcpc pid), exactly as the forwarder fault needs
`rndc flush`.

## Split-horizon namespace

```
sme01.internal      served by dns-int, authoritative
  dns-int  user1  guest1  finance1  admin1        <- internal endpoints only

sme01.example       served by dns-dmz, authoritative, no recursion
  ns   -> 10.10.41.10
  www  -> 10.10.40.10    mail -> 10.10.40.10
  web1 -> 10.10.40.10    app1 -> 10.10.50.10      <- the DMZ applications
```

**The DMZ applications are not in the internal zone.** `dns-int` is
authoritative for the internal endpoints and nothing else; it reaches `web1`
and `app1` by **forwarding `sme01.example` to `dns-dmz`**
(`type forward; forward only; forwarders { 10.10.41.10; }`). There is one
authoritative copy of a DMZ name, held by the DMZ, and an internal client and
an outside client are answered from it alike.

That is a deliberate constraint, not an accident of the zone file: putting
`web1 IN A 10.10.40.10` back into `db.sme01.internal` would have `dns-int`
answer authoritatively and short-circuit the forward, which is precisely what
the split horizon exists to prevent. The zone file says so in a comment.

### The forward is on the firewall's critical path

Because the DMZ names now resolve only through it, the two `INTERNAL -> DMZ`
rules that carry it — `resolver-forward-udp` and `resolver-forward-tcp`, rules
100 and 110, scoped to `10.10.60.10 -> 10.10.41.10:53` — are load-bearing for
name-based access to every DMZ service. Verified by disabling both:

```
rules 100+110 disabled   user1 -> web1.sme01.example      FAILS
                         user1 -> user1.sme01.internal    resolves  (local zone)
rules re-enabled         user1 -> web1.sme01.example      resolves
```

No other rule was needed for this: the resolver-to-DMZ flow was already the
only path across the boundary, and the service rules themselves are unchanged.

### Short names still work, through option 119

Moving the DMZ names into `sme01.example` means `web1.sme01.internal` is now
NXDOMAIN. The lease therefore carries a **search list** — `domain-search`,
option 119 — with both namespaces, so a client typing `web1` tries
`web1.sme01.internal`, gets NXDOMAIN, and then finds `web1.sme01.example`:

```
user1:/etc/resolv.conf   search sme01.internal sme01.example
                         nameserver 10.10.60.10
```

BusyBox `udhcpc` does not request option 119 unless told to, so the client is
started with `-O search`; `dhcp/udhcpc.script` already prefers `$search` over
`$domain`. The statically addressed hosts that use the internal resolver get
the same two-entry list written into `/etc/resolv.conf`.

Verified end to end:

```
user1     -> web1                  @10.10.60.10   10.10.40.10  (via the search list)
user1     -> web1.sme01.example    @10.10.60.10   10.10.40.10  (non-authoritative:
                                                                forwarded to the DMZ)
user1     -> web1.sme01.internal   @10.10.60.10   NXDOMAIN     (by design)
user1     -> user1.sme01.internal  @10.10.60.10   10.10.10.10  (internal, authoritative)
external1 -> www.sme01.example     @10.10.41.10   10.10.40.10
```

### Resolution does not widen access

Every endpoint can *resolve* every DMZ name; what it may *reach* is unchanged
and still decided by the policy on source, destination and port:

```
              web1.sme01.example:80    app1.sme01.example:5201
user1                OPEN                      OPEN
guest1               OPEN                      BLOCKED
finance1             BLOCKED                   OPEN
admin1               OPEN                      OPEN
```

And the resolver itself is the only way in: `user1` and `finance1` cannot query
`dns-dmz` directly at all, so "going through `dns-int`" is enforced by the
firewall rather than merely conventional. (`admin1` can, through its blanket
`admin-to-dmz` rule.)

## How the zone files are wired

The reference configuration lives in `dns/internal/` and `dns/dmz/`, mounted
**read-only under `/opt/dns-src`**. The healthy state copies it into
`/etc/bind` and reloads named.

That indirection matters: a fault has to edit the running configuration, and a
bind mount is the same inode as the file in the repository. Mounting straight
onto `/etc/bind/named.conf.local` would mean an injected fault rewrites a
tracked file. Copying gives a writable working copy whose pristine source is
always one `cp` away, which is exactly what every restore does.

## Nameservers are services, not endpoints

`dns-int` and `dns-dmz` carry the `service` role in
`sme_leaf_spine_dmz_dns.yaml`, which puts them in `NON_ENDPOINT_ROLES` and so
outside `endpoint_nodes()`. That is the modelling claim: a nameserver is
something the endpoints *consume*, not a user of the network being evaluated.

What it changes:

- an endpoint-scoped fault (`{node: endpoint}` — interface down, address
  removed, wrong gateway, low MTU) can no longer land **on** a nameserver;
- a blast radius never names one, so `dns-int` is no longer listed as collateral
  of a leaf1 fault it merely sits behind;
- `segment_hosts()` is empty for `dns_internal` and `dns_dmz`, which takes those
  two segments out of the subnet- and route-scoped fault pools.

What it does not change: the `dns_resolution` family binds by role
(`dns_resolver`, `dns_authoritative`), not by endpoint class, so all four
methods still target the nameservers exactly as before. And reachability *to*
them is still asserted — through the explicit `connectivity_requirements`
below, which is why the check count stays at thirteen.

## The ping asymmetry

`ubuntu/bind9` ships no `ping`. The name servers can be probed but cannot probe,
so their `connectivity_requirements` are declared `bidirectional: false`. That
also matches what a name server does: it is queried, it does not initiate.

One consequence: the `dns-int -> dns-dmz` forwarding path is real but not
ICMP-testable, so no connectivity check covers it. It is verified by resolving a
`sme01.example` name through the internal resolver, which is the meaningful test
anyway.

## Files

- `topology.clab.yml`: containers, images, management network (`172.24.0.0/24`),
  and the pinned MACs of the four DHCP clients;
- `dns/internal/`, `dns/dmz/`: named options, zone declarations, zone files;
- `dhcp/udhcpc.script`: the client-side lease hook, bind-mounted read-only into
  the four DHCP clients;
- `states/healthy.json`: 395 commands (34 `shell`, 62 `srl_cli`, 299 `vyos_cli`);
- `states/generate_healthy_state.py`: generator — edit the host, routing, zone
  and policy tables there, not the JSON.

## Deploy, apply, verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-dns/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/sme01-dns/states/healthy.json \
  --topology scenarios/topologies/sme_leaf_spine_dmz_dns.yaml \
  --verify --verify-timeout 120
```

The VyOS node boots on systemd and takes noticeably longer than SR Linux; allow
a couple of minutes before applying state. Observed on a fresh deployment:
`verify=True` on all thirteen checks in one pass, with the four client endpoints
addressed entirely by DHCP.

```bash
docker exec clab-sme01-dns-firewall /opt/vyatta/bin/vyatta-op-cmd-wrapper \
  show dhcp server leases
docker exec clab-sme01-dns-user1 nslookup web1.sme01.example 10.10.60.10
docker exec clab-sme01-dns-dns-int rndc status

# the lease, end to end: no @server, the resolver came from option 6
docker exec clab-sme01-dns-user1 getent hosts web1   # via the search list
docker exec clab-sme01-dns-user1 sh -c 'ip -4 addr show eth1; cat /etc/resolv.conf'

docker exec -i clab-sme01-dns-firewall vbash -s <<'CMD'
source /opt/vyatta/etc/functions/script-template
configure
show firewall
exit
CMD
```

**Never `docker restart` a lab node**: containerlab creates the veth pairs at
deploy time and a restart loses them, leaving the node with no data interface.
Redeploy instead.

## Scenario applicability

Two families were excluded when the spine pair became a firewall, recorded in
`scenarios/topology_applicability.json`:

- `connectivity/wrong_routing_table.yaml` — `create_static_route_loop`
  needs a router peer of the target that is not itself the destination's
  gateway. Each leaf now has exactly one router peer, the firewall, which is
  also the gateway of every DMZ and outside destination. Structurally
  inexpressible, the same way it is on `sme_leaf_spine_dmz_vlan`.
- `security/tcp_syn_flood.yaml` — needs an attacker attachable to a segment in
  the `external` zone, and the internet segment is now a point-to-point cable
  between the firewall and `external1`. An attacker port on that segment would
  make it applicable again.

Everything else still compiles and materialises: 23 connectivity instances and 2
QoS instances render with no error, and the netarena families that bind
`{node: router}` now emit `vyos_cli` on the firewall through the existing VyOS
operation templates.

## Known gaps

**The deny rules are not verified by the benchmark.**
`connectivity_requirements` can only say "must reach"; there is no way to
express "must not reach", so the healthy state configures the DMZ boundary, the
OUTSIDE deny and the per-port restrictions, and the oracle checks none of them.
They were verified by hand here, and a regression would pass unnoticed. Two
consequences: a scenario that *relaxes* the policy to repair connectivity would
score as a success, and the ICMP allowances above exist purely because the
oracle pings.

**The DNS faults are invisible to the oracle** for the same reason: resolution
can be completely broken while all thirteen checks stay green.

**The firewall does not protect itself.** No `local` zone is defined, so the
zone policy governs forwarded traffic only and the firewall's own management
services stay reachable from every zone — `203.0.113.1:22` answers from
`external1`. Closing that needs a local zone with an explicit
`INTERNAL -> local` allowance for the relayed DHCP (udp/67) and a
`local -> INTERNAL` allowance for the replies, plus a decision about the
containerlab management interface, which belongs to no zone.

These are all item 4 of `notes.txt`: the oracle needs service-aware probes and a
way to assert unreachability, not only ICMP.

## Validated fault methods

Every method the catalog can materialise against this topology was injected on
a freshly deployed lab, its blast radius compared with the declared expectation,
and restored: **22 faults, 21 recovered by their own restore commands**, one by
a healthy re-apply (see below).

| Family | Methods | Target | Failing checks | Restored |
|---|---|---|---|---|
| `dns_resolution` | m1–m4 | dns-int, external1 | 0 — invisible to a ping oracle | yes |
| `disable_interface` | m1 | firewall `eth3` | 6 (`web1`) | yes |
| `disable_interface` | m2, m3 | web1, app1 | 6, 5 | m3 yes, m2 see below |
| `disable_routing` | m1–m4 | leaf1, leaf2, guest1, user1 | 8, 2, 2, 5 | yes |
| `drop_traffic_to_from_subnet` | m1–m4 | firewall `eth5` | 5 (`app1`) | yes |
| `remove_ip` | m1–m5 | leaf1, user1, web1, app1 | 2, 5, 6, 5, 6 | yes |
| `qos.link_impairment` | m1, m2 | guest1 | 0, 2 | yes |

The four `dns_resolution` methods are configuration errors — nothing is crashed,
a setting is made wrong — and were additionally checked by **resolution**:

| Method | Target | Broke | Restored |
|---|---|---|---|
| `resolver_zone_unserved` | dns-int | `user1 -> user1.sme01.internal` | yes |
| `dns_record_removed` | dns-int | `guest1 -> user1.sme01.internal` | yes |
| `dns_forwarder_unreachable` | dns-int | `user1 -> web1.sme01.example` | yes |
| `endpoint_wrong_resolver` | finance1 | `finance1 -> user1.sme01.internal` | yes |

`named` is **PID 1** in these containers, so it cannot be killed — a stopped
service is not an expressible fault here. Every method is therefore a
configuration error, which also matches the direction recorded in `notes.txt`:
faults that are misconfigurations rather than attacks.

The forwarder fault needs `rndc flush` alongside the reload. Without it the
resolver keeps answering from cache for the whole 300 s TTL and the fault looks
like it did nothing.

### Two gaps the sweep exposed, neither of them new

Both predate the firewall and both reproduce on `sme_leaf_spine_dmz_small`;
they are recorded here because this sweep is what made them visible.

**`endpoint_interface_down` does not restore what it broke.** The fault is
`ip link set dev eth1 down` and the restore is `ip link set dev eth1 up`. The
kernel drops the manually added default route when the link goes down and does
not put it back, so `disable_interface.m2` left `web1` addressed, up, and with
no default route — six checks still failing after a "successful" restore. Only
re-applying the healthy state recovered it. The restore needs to re-add the
route, or reuse the endpoint's healthy-state commands.

**A fault on a nameserver segment declares an empty blast radius.**
`segment_hosts()` filters to evaluated endpoints, and the nameservers are
`service` nodes, so a fault on `dns_internal` yields `affected_nodes: []` —
while `user1 -> dns-int` and `app1 -> dns-int` are real connectivity
requirements that do fail. Measured: `remove_ip.m1` removes `10.10.60.1` from
leaf1, declares nothing affected, and breaks two checks. On `HEAD` the same
understatement occurs on 7 of 39 seeds; seed 9 simply happens to land on it now.
The blast radius should be computed from the connectivity requirements, not from
the endpoint class.

## Destroy

```bash
containerlab destroy -t benchmarks/testbeds/containerlab/sme01-dns/topology.clab.yml --cleanup
```
