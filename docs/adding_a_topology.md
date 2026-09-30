# Adding A Topology

Adding a topology costs three declarative files, or a compiler change. Which one
you are in depends on a single question: **does the new topology reuse a
forwarding model the framework already knows?**

This document is the checklist. [`scenario_pipeline.md`](scenario_pipeline.md)
describes the compilation path itself.

## What "forwarding model" means

It is the answer to three linked questions: at which layer the forwarding
decision is taken, on which device, and **in which configuration object** it is
written. The third one is what matters here — a fault scenario does not break
"the network" in the abstract, it modifies a precise configuration object. If
that object does not exist, the scenario has nothing to break.

Three models ship today:

| | `sme01-small` — routed | `sme01-vlan` — switched |
|---|---|---|
| Decision on the leaf | IPv4 route table | MAC table |
| Leaf holds an access IP | yes, `10.10.10.1/24` | no IPv4 on the data plane |
| Endpoint gateway | the leaf | the spines |
| Object carrying the decision | `static-routes`, `next-hop-groups` | `network-instance <vlan>` of type `mac-vrf` |
| L3 hops `user1` to `web1` | 2 | 1 |

A third, `sme01-fw`, replaces the spine pair with a single VyOS firewall: same
layer-2 leaves, but the gateway is also a policy enforcement point with
INTERNAL / DMZ / OUTSIDE zones, and it introduces the `vyos` node kind.

The same intent compiles to configurations with nothing in common:

```text
routed     leaf2 :: set / interface ethernet-1/40 admin-state disable
switched   leaf2 :: delete / network-instance vlan40 interface ethernet-1/40.0
```

`forwarding_model` in `scenarios/topology_applicability.json`
records this, but no code reads it. What drives behaviour is the explicit
`topologies` list on each scenario entry.

### What is *not* a new model

These stay routed and cost three files:

- more leaves, spines, or endpoints;
- OSPF or BGP instead of static routes — the decision stays L3 on the same
  device, only `healthy.json` changes;
- different addressing or different VLAN IDs on a routed fabric.

EVPN-VXLAN would be a third model: the decision returns to L3 on the leaves, but
through an overlay whose objects neither existing path can express.

## Case A — Reusing an existing forwarding model

Three declarative files, no code.

| # | File | Role |
|---|---|---|
| 1 | `benchmarks/testbeds/containerlab/<lab>/topology.clab.yml` | Cabling, images, management network |
| 2 | `benchmarks/testbeds/containerlab/<lab>/states/healthy.json` | Reference state: the commands that configure the fabric |
| 3 | `scenarios/topologies/<id>.yaml` | Executable descriptor: the bridge between abstract and concrete |

File 3 does the work. It carries the real interface names, the roles, the
segments, and the required flows; the compiler resolves every selector against
it.

Give the lab its own management subnet so it can run alongside the others
(`sme01-small` uses `172.21.0.0/24`, `sme01-vlan` uses `172.22.0.0/24`).

### Minimum descriptor content

| Field | Required | Why |
|---|---|---|
| `topology.id` | yes | Key used by the applicability map |
| `environment.{type,lab_name,topology_path}` | yes | Link to the containerlab file |
| `nodes[].kind` | yes | `nokia_srlinux` or `linux`; selects command rendering |
| `nodes[].interfaces[].{segment,ipv4}` | yes | IPv4 addresses must be **unique across the whole topology** |
| `segments[].members` | yes | A node must appear in the members of every segment it has an interface on |
| `connectivity_requirements` | yes | `connectivity_checks()` raises without it |
| `gateway` role | **exactly one per endpoint segment** | Otherwise: `segment 'X' must have exactly one gateway` |
| `services`, `optional_nodes` attacker | for the security domain | Security scenarios bind through them |

### Which fields are functional

Not every field present in an existing descriptor is read by code. Checked by
grep against `scenarios/`, `benchmarks/platforms/`, `benchmarks/`:

```text
functional    vlan_id  bridge_domain  tagged_vlans  attached_endpoint
              security_zone  attachable_segments  type  members  roles
documentary   gateway_ipv4  gateway_node  standby_gateway_node
              capacity_mbps  forwarding_model
```

Copying a documentary field will not make the compiler behave differently.

### Four traps

1. **One gateway per segment.** Marking both spines `gateway` makes every
   endpoint-scoped `remove_ip` method refuse to compile.
2. **`endpoint_nodes()` excludes the `admin_client`, `transit`, and `service`
   roles.** A management client never appears in the connectivity checks, so a
   fault expectation naming it can never be observed; a `service` node — a
   nameserver, say — is consumed by the endpoints rather than evaluated as one,
   and its reachability is asserted through an explicit connectivity
   requirement instead.
3. **`type: management` segments are excluded** from `interfaces()` by default.
4. **An empty blast radius disables judging.** `_connectivity_impact` returns
   early when `expected_affected_nodes` is empty, so the scenario passes without
   being verified. Prefer a binder that refuses to select such a target.

### Verify

```bash
containerlab deploy -t benchmarks/testbeds/containerlab/<lab>/topology.clab.yml

./scripts/apply_healthy_state.py \
  --state benchmarks/testbeds/containerlab/<lab>/states/healthy.json \
  --topology scenarios/topologies/<id>.yaml \
  --verify --verify-timeout 90
```

`verify=True` means the reference state converges and every declared flow works.

## Case C-bis — Adding a node kind

A new platform is a separate axis from a new forwarding model, and it is not the
descriptor that carries the cost: it is the adapter. Six files dispatch on
`kind`, and missing one fails in a different way each time:

| File | What it decides | If you forget it |
|---|---|---|
| `state.py` | the command mode of the healthy state | the state cannot express the platform |
| `executor.py` | how a batch reaches the device | — |
| `env.py` | `apply_state` and `execute` dispatch | state applies, agent commands do not |
| `injector.py` | how a *fault* reaches the device | faults are sent to a shell and silently fail |
| `observer.py` | raw observations | the node is missing from the observation |
| `ani.py` | what the **agent** can read and write | reads fall back to Linux output, hiding the platform's own objects |

The `ani.py` branch is the one to get right. `get_running_config` falls back to
`ip address show`, which looks plausible and shows addresses and routes — while
hiding zones, policies, or anything else the scenario is actually about.

`injector.py` was the one missed while adding `vyos`: faults reported success
and changed nothing, because `run_shell` happily accepted a VyOS command.

`endpoint_nodes()` and `segment_hosts()` filter on `kind in {"host","linux"}`, so
a new network-device kind is excluded from endpoints with no change — but a
firewall declared as `kind: linux` **would be counted as an endpoint**, and would
corrupt every blast radius. The escape hatch for a Linux node that is not an
evaluated endpoint is a role in `NON_ENDPOINT_ROLES`: `transit` for one that
forwards, `service` for one the endpoints consume.

### Make the executor provide a real transaction boundary

SR Linux commits a candidate atomically. Raw VyOS configure scripts do not: an
invalid `set` can be skipped while valid sibling commands are committed, and the
script may still exit 0. Scanning `Set failed` after an unconditional commit is too
late because active configuration may already contain the valid subset.

The containerlab executor therefore parses VyOS commands into `set`/`delete` token
vectors and sends them as JSON to a fixed in-container `ConfigSession` driver. The
driver applies every vector to one private candidate, commits once, and discards the
candidate if any operation fails. New platforms need an equivalent invariant:
`ok=False` must mean the group did not commit. If a transport timeout makes that
unknowable, report an indeterminate outcome rather than an ordinary failure.

## Case B — Running the benchmarks on it

Two composable files, modelled on `benchmarks/configs/testbeds/sme01-small.toml` and
`benchmarks/configs/experiments/connectivity-smoke.toml`. The testbed is where lab and
descriptor are joined by hand, and it is the only place they are:

```toml
# benchmarks/configs/testbeds/<lab>.toml
id                  = "<lab>"
platform            = "containerlab"
topology_descriptor = "scenarios/topologies/<id>.yaml"
topology_file       = "benchmarks/testbeds/containerlab/<lab>/topology.clab.yml"
reference_state     = "benchmarks/testbeds/containerlab/<lab>/states/healthy.json"
```

The experiment then names a scenario and points at that testbed:

```toml
# benchmarks/configs/experiments/<name>.toml
scenario_id    = "connectivity.<scenario>.m1"
testbed_config = "benchmarks/configs/testbeds/<lab>.toml"
```

A lab that needs two reference states -- a greenfield one, say -- gets a second
testbed file rather than a second experiment key; `sme01-qos-greenfield.toml` is the
example.

## Case C — Introducing a new forwarding model

The trigger is mechanical: **do the existing selectors designate anything
meaningful, and are the rendered commands valid?** If not, the engine must be
extended, in this order.

| Layer | File | What changes |
|---|---|---|
| Vocabulary | `schemas/scenario.schema.json` | New `selector.node` / `selector.interface` values |
| Selection | `scenario_compiler/topology.py` | Helpers answering "which interfaces, which endpoints behind them" |
| Binding | `scenario_compiler/compiler.py` | A binder producing target, interface, and blast radius |
| Rendering | `benchmarks/platforms/containerlab/compiled_topology.py` | Operations and their reference restores |
| Scenarios | `scenarios/<domain>/*.yaml` | Families carried over, and families replaced |
| Routing | `scenarios/topology_applicability.json` | Which scenario applies where, and why not |

A scenario **absent** from the applicability map compiles against every topology,
so the map only has to record what is restricted.

### Diagnosing where you stand

Which scenarios fail to bind, and why:

```python
from scenarios.compiler.compiler import ScenarioCompiler
from scenarios.compiler.loader import discover_scenarios

for domain, path, scen in discover_scenarios("scenarios"):
    c = ScenarioCompiler.from_topology_file("scenarios/topologies/<id>.yaml", seed=7)
    try:
        c.compile_scenario(domain, scen, source_path=path)
    except Exception as exc:
        print(f"{domain}/{path.name}: {exc}")
```

A binding failure means case C or an applicability entry. A binding that
succeeds but renders invalid CLI means the rendering layer.

### Proving you broke nothing

Rendering is deterministic given a seed, so the existing topologies can be
diffed byte for byte against `HEAD`:

```bash
git worktree add /tmp/baseline HEAD
# dump every materialized command for a topology over N seeds, both revisions
# (compile_scenario + materialize, keyed by seed and instance id), then diff.
git worktree remove /tmp/baseline --force
```

This is what confirmed the subinterface split left the routed topologies
untouched: 720 renders per topology, identical.

Then add tests under `scenarios/tests/` that lock the distinction — that
a routed port still renders without an explicit subinterface, and that the new
path renders what the device accepts.

### Validate against the device, not the diff

A command that commits is not a command that works. Two failure modes seen on
`sme01-vlan`, neither of which any unit test would have caught:

- an ACL bound to a tagged trunk fails the **whole** candidate transaction, so
  the fault injects "successfully" and does nothing;
- SR Linux keeps a **shared candidate** across sessions: a failed commit from an
  earlier manual test keeps poisoning later commits until `discard now`.

So inject every method on a fresh lab, check the blast radius, and restore:

```text
[OK] switch_access_port_down
     expected affected : ['web1']
     failing checks    : 6
     after restore     : healthy
```

## Order of work

```text
1. topology.clab.yml + healthy.json    -> containerlab deploy, apply --verify
2. executable descriptor                -> compile_scenarios() on every topology
3. scenarios that will not bind         -> entry in topology_applicability.json
4. rendering that produces invalid CLI  -> only here, touch the code
5. benchmark .toml configs              -> run it
```

The pivot is step 3 to 4. Up to step 3 a topology costs three files and an
afternoon. `sme01-vlan` went to step 4 because its leaves stopped being routers:
the selectors designated nothing, and the templates rendered CLI the device
rejects.

## Worked example

`sme01-vlan` is the switched reference. See
[`benchmarks/testbeds/containerlab/sme01-vlan/README.md`](../benchmarks/testbeds/containerlab/sme01-vlan/README.md)
for its forwarding model, its VLAN and address plan, why gateway redundancy is
not VRRP, and the twelve fault methods validated against it.
