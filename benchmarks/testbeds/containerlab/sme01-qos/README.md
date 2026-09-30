# sme01-qos — routed leaf-spine with a shaped WAN edge

The QoS reference lab. It is `sme01-small`'s routed fabric with one structural
addition: a Linux WAN edge, `wan1`, attached to both spines and facing the
simulated Internet. Its egress interface carries a real bottleneck, and that
bottleneck is the only reason any QoS intent in this repository can be judged.

## Forwarding model

Routed, exactly as `sme01-small`: routed access interfaces on the leaves, static
routes with ECMP through the spines. Nothing about layer 2 or layer 3 changes.
What changes is that the spine pair now carries a third peer — a border router:

```
                    ┌─ spine1 ─┐
   user1  ─┐        │          │         ┌─ finance1
   guest1 ─┼─ leaf1 ┤          ├─ leaf2 ─┼─ web1
   admin1 ─┘        │          │         └─ app1
                    └─ spine2 ─┘
                        │  │
                        │  │                 wan1 (linux)
                        │  │        ┌─────────────────────────────┐
                        │  └────────┤ eth1  10.255.1.1  ← spine1  │
                        └───────────┤ eth3  10.255.1.3  ← spine2  │
                                    │                             │
                                    │ eth2  203.0.113.1 ──────────┼── external1
                                    └──────────────┬──────────────┘
                                                   │
                                         tc htb, 10 Mbit circuit
                                           1:10 assured  8 Mbit  (src 10.10.10.0/24)
                                           1:20 default  2 Mbit  (everything else)
```

* **Protected flow** — `user1 → external1`, the business traffic.
* **Competing flow** — `guest1 → external1`, the bulk guest traffic.

Both leave through `wan1:eth2`, which is where the policy lives.

## Why the WAN edge sits on the spines

A border router belongs beside the leaves in the fabric, not behind one of them,
and that is how an enterprise cables its Internet edge. Hanging `wan1` off
`leaf2` made a single access leaf the sole path to the Internet: `user1`'s
traffic climbed to a spine, came back down to `leaf2`, and left again, and any
`leaf2` failure took the uplink with it. Dual-homed to `spine1` and `spine2`,
both leaves reach it over equal-cost paths and no single device outage isolates
the Internet.

The two uplinks are `eth1` (spine1) and `eth3` (spine2). `eth2` stays the
Internet-facing shaped port through the move on purpose: the descriptor's
`qos_policy` names that interface, and that name is the single spelling shared
by the healthy states, every fault method, every reference restore and the
oracles. Renumbering it would have bought tidier interface numbers at the price
of the invariant the whole lab rests on.

Next-hop-group names follow the descriptor's `to-{gateway}` template, where the
gateway is the node owning the *destination* segment rather than the next hop
taken to reach it. Both leaves therefore route `203.0.113.0/24` through a group
named `to-wan1` whose two next hops are the spines; each spine's own `to-wan1`
holds the single directly-attached uplink.

## Why a Linux WAN edge and not SR Linux

`tc` is enforced by the container's own kernel, so the shaping is real, the
throughput is measurable, and the restore is a handful of idempotent commands.
SR Linux accepts a queue and scheduler configuration in this image, but nothing
guarantees its containerised datapath honours it — a throughput oracle would
then report "not repaired" no matter what the agent configured.

`wan1` deliberately carries neither the `router` role nor the `acl` capability.
`router` would expose it to the routed connectivity families, which render SR
Linux CLI; `acl` would let the subnet-policy binder choose it as an ACL-capable
gateway. It carries `transit`, which is what keeps it out of `endpoint_nodes()`
and therefore out of every blast radius it merely forwards through.

## Circuit and policy are different things

The 10 Mbit rate is the **circuit** — the uplink speed. The two classes are the
**policy**. They are rendered separately and only the policy is ever broken.

If a fault could remove the circuit, wiping the shaping would *raise* the
protected flow from 8 Mbit/s to the fabric's ~65 Mbit/s, and the fault would
reward the failure it is supposed to model. So every scenario leaves a congested
10 Mbit circuit standing.

Isolation comes from `rate`, not `ceil`: both classes may burst into the whole
circuit while it is idle, and HTB shares a congested circuit in proportion to
the guaranteed rates. Measured on this lab:

| | class 1:10 (assured) | class 1:20 (default) |
|---|---|---|
| background alone | 0 | 10.2 Mbit/s — borrows the idle circuit |
| both flows | **8.9 Mbit/s** | 2.6 Mbit/s |

## Two healthy states

The lab serves two scenario families that disagree about one thing only:
whether the class policy already exists.

| State | `wan1:eth2` | Family |
|---|---|---|
| `states/healthy.json` | circuit **and** the two-class policy | `wan_shaping_policy_repair` |
| `states/healthy_greenfield.json` | circuit only, one flat default class | `assured_bandwidth` |

Both come out of `states/generate_healthy_state.py`, and neither spells the
policy out: it is rendered from the descriptor's `qos_policy` block by the same
function the reference restore uses, so a restore can never rebuild something
the healthy state did not contain.

```bash
./benchmarks/testbeds/containerlab/sme01-qos/states/generate_healthy_state.py
```

## Traps this lab walked into

Four of these cost real debugging time and are worth knowing before changing
anything here.

1. **Endpoints come up at MTU 9500.** containerlab's default. A jumbo endpoint
   behind a fabric that does not carry jumbo frames blackholes PMTU discovery:
   ping is perfect, TCP collapses to ~0.5 Mbit/s with retransmits, and every
   throughput number is meaningless. Both healthy states pin `mtu 1500`. The
   descriptor's `endpoint_interface_mtu: 1500` is documentary — nothing applied
   it before this lab.

2. **`tc qdisc replace` is not idempotent for HTB.** Replacing a root HTB with
   another HTB of the same handle is rejected with `EINVAL`. Every renderer
   here tears the root down first (`tc qdisc del … || true`) and then adds, so
   re-applying a healthy state or restoring a fault that left the qdisc in place
   both work.

3. **`iperf3 -b` is per stream.** `-b 30M -P 4` offers 120 Mbit/s, which
   saturates the leaf-spine fabric (~65 Mbit/s) *upstream* of the shaper and
   starves the protected flow in a way no QoS policy could fix. The background
   load is one stream at 15–25 Mbit/s: enough to congest a 10 Mbit circuit,
   not enough to melt the path to it.

4. **`pkill -f 'iperf3 -c'` kills its own shell.** The pattern matches the
   killing `sh -c`'s own argv, so pkill signals itself and the command returns
   143 — a restore that worked, reported as a failure. Matching the process
   name with `pkill -x iperf3` cannot self-match.

There is a fifth, inherited: `run_shell` execs after `shlex.split` with **no
shell**, so `&`, `||` and redirections are passed to the binary as literal
arguments. Anything needing shell grammar goes through `detached_shell()`.

## Management subnet

`172.26.0.0/24`. The repository reserves `172.20`–`172.24` for its other labs
and `172.25` was taken by an unrelated Docker network on the development host.

## Verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/sme01-qos/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/sme01-qos/states/healthy.json \
  --topology scenarios/topologies/sme_wan_edge_qos.yaml \
  --verify --verify-timeout 120
```

Fourteen connectivity checks, including `user1 ⇄ external1` and
`guest1 ⇄ external1`, which are the flows that cross the WAN edge in both
directions.

Then confirm the bottleneck actually separates the classes:

```bash
docker exec clab-sme01-qos-guest1 \
  sh -c 'setsid iperf3 -c 203.0.113.10 -p 5202 -u -b 20M -t 30 -P 1 >/dev/null 2>&1 </dev/null &'
docker exec clab-sme01-qos-user1 iperf3 -c 203.0.113.10 -t 10
docker exec clab-sme01-qos-wan1  tc -s class show dev eth2
docker exec clab-sme01-qos-guest1 sh -c 'pkill -x iperf3 || true'
```

The class counters are the authoritative view: `iperf3`'s own figure is dragged
down by TCP slow start inside a short window, while `tc` reports what the shaper
actually delivered.

## Validated fault methods

Injected one at a time on a converged lab, with `user1 → external1` (TCP) and
`guest1 → external1` (UDP, 20 Mbit/s) both running, then restored. Figures are
the shaper's own class counters over a 12 s window, in kbit/s.

| method | assured: baseline → faulted → restored | default when faulted |
|---|---|---|
| `shaping_policy_removed` | 7421 → **0** → 4708 | 10059 |
| `assured_class_starved` | 5768 → **751** → 7716 | 9273 |
| `assured_classifier_removed` | 6576 → **0** → 8027 | 10070 |
| `class_allocation_inverted` | 6678 → **2011** → 6887 | 8036 |

Every row totals ~10 050 kbit/s, faulted included: the circuit is never what
breaks. Zero failing connectivity checks after each restore, and the restored
`tc class`/`tc filter` state is identical to the healthy state.

`class_allocation_inverted` swaps the two guarantees rather than raising the
best-effort one. Raising it was tried first and is wrong: children whose rates
outrun the parent's make HTB over-commit, the measured total reached 21 Mbit/s
on a 10 Mbit circuit, and the protected flow came out *faster* than at baseline.

### What the counters cannot show

`assured_bandwidth` is greenfield, so there is no assured class to read: both
flows sit in the single default class and the counters only prove the circuit is
saturated. Distinguishing the protected flow from the competing one there needs
a per-flow throughput oracle, which is a separate piece of work.

One measurement trap to inherit: an iperf3 server handles one test at a time,
and a starved TCP test that times out leaves the session wedged, so every later
probe on that port reads zero. Restart the servers between measurements.
