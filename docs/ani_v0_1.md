# Agent-Network Interface (ANI) v0.1 and v0.2

v0.2 (2026-09-17) adds two operations, `get_object` and `update_object`, and changes
nothing else; the version a record carries (`ani_version`) says which surface its agent had.

ANI v0.1 is the single interface between an autonomous System Under Test (SUT)
and the ContainerLab environment. It is intentionally independent of the
benchmark domain: Connectivity, QoS, and future Security tasks use the same
operations.

```text
Judge -- A2A task, public SLO, time budget --> SUT
SUT   -- ANI v0.1 operations --> ContainerLab adapter
Judge -- independent domain oracle --> final score
```

This design follows the useful separation in the IETF NetConfBench draft between
an agent-network interface and an independent task-evaluation interface. The
local API can later be exposed as MCP tools without changing the A2A envelope or
the operation semantics. See [draft-cui-nmrg-llm-benchmark-02, section 4.2](https://www.ietf.org/archive/id/draft-cui-nmrg-llm-benchmark-02.html).

## Public A2A Context

The Judge sends an opaque scenario ID, natural-language intent, safety
constraints, a minimal public observation, public success criteria, and a
wall-clock budget:

```json
{
  "execution_budget": {"wall_clock_seconds": 300},
  "ani": {
    "version": "ibn_eval.ani.v0.2",
    "operations": [
      "get_topology",
      "get_state",
      "get_running_config",
      "get_object",
      "update_config",
      "update_object",
      "execute_validation",
      "rollback_config"
    ]
  }
}
```

`wall_clock_seconds` is the experiment budget. It replaces a fixed number of
agent steps: the SUT may inspect, act, validate, and revise as often as it can
within that time. The Judge HTTP timeout must be slightly larger than this
budget to receive the final report.

## Operations

| Operation | Purpose |
|---|---|
| `get_topology` | Discover nodes, links/segments, roles, capabilities, and interface names. |
| `get_state` | Read operational state such as interfaces, routes, qdisc state, or system information. |
| `get_running_config` | Read active configuration for selected nodes and optional paths, as CLI text (default) or as structured JSON. |
| `get_object` (v0.2) | Read one named object on one node, with the narrowest command the platform has: an `interface`, a `route` (prefix), an `arp` entry (IPv4 address), a `mac` entry, the `qos` policy of an interface, a `firewall_rule` set, a `zone` or a `dhcp_pool`, named as the node writes them. `found: false` with the node's own words when there is no such object; an error only for a request the ANI cannot form. Small answers where `get_state` and `get_running_config` return whole views. The result names the command it ran and its `mode`: `scoped` (the command names the object), `filter` (a listing kept to the lines that mention it) or `config` (the `set` lines of the object's own configuration subtree on VyOS). |
| `update_config` | Apply native configuration commands to one or several nodes. The ANI selects Linux shell, SR Linux candidate CLI, or a VyOS candidate session from node kind. |
| `update_object` (v0.2) | Change one named object on one node through attributes: `interface` (admin_state, mtu, address with address_action), `route` (next_hop, next_hop_group on SR Linux, delete), `routing` (admin_state of an SR Linux network-instance), `acl` (delete with unbind_interfaces), `dhcp_pool` (subnet with subnet_id, default_router, name_server, domain_name, domain_search, lease, static_mapping, delete_option; delete), `firewall_rule` (rule with action or delete, default_action), `zone` (from_zone with ruleset, delete_from, add_interface, delete_interface), `qos` (delete_root, htb, class on Linux). The ANI compiles the native commands for the node's platform (`benchmarks/platforms/containerlab/object_writes.py`) and applies them through `update_config`, so the safety preflight, the transaction and `rollback_config` are the same; the result adds `compiled` (the commands sent, the derived compensation and the steps skipped). Before writing, a step whose compensation is derived from the request alone is checked against the object as it is (one `get_object` read): a step the object already satisfies is skipped, so a repeated request changes nothing and is never "undone" into a worse state, and SR Linux never receives a candidate with nothing to commit. A request the ANI cannot form (unknown attribute, a value that is not an address, a kind the platform lacks) is refused by name before any device sees it. |
| `execute_validation` | Run explicit ICMP probes or evaluate the public task success criteria. |
| `rollback_config` | Execute compensating commands recorded when a prior `update_config` transaction was submitted. |

An operation is generic even when the native command is vendor-specific. For
example, the agent sends a native SR Linux command through `update_config`; it
never calls `sr_cli` itself. The adapter owns the transport and configuration
session for SR Linux, VyOS, and Linux targets.

### Structured output

`get_running_config` accepts `format`, either `text` (default, unchanged) or
`json`. In JSON form it ignores `paths` and returns whole-device data in the best
native form each supported platform exposes. SR Linux returns its parsed running
datastore and interface, LLDP, network-instance, system, and platform state
subtrees. VyOS returns the parsed running configuration plus its version document.
Linux has no management datastore, so `running` contains named outputs from the
kernel's `ip` and `tc` interfaces. An unsupported node kind returns `ok: false`
with an explicit reason rather than plausible but incomplete fallback output.

This exists so that a subject that reasons over a structured world model builds
it from evidence fetched **through the ANI**. Agent-selected reads are counted in
`ani_operations` and `tool_call_count`.

```json
{
  "changes": [
    {
      "target": "leaf2",
      "commands": [
        "set / interface ethernet-1/40 admin-state enable"
      ],
      "rollback_commands": [
        "set / interface ethernet-1/40 admin-state disable"
      ],
      "reason": "Observed access interface is administratively down."
    }
  ]
}
```

A request may contain changes for several devices, but each `changes` entry is a
separate native commit boundary. The ANI validates the complete request, including
every compensation, before the first write and stops at the first failed entry.
SR Linux and VyOS apply a whole entry through one candidate session. A Linux entry
is limited to one command because Linux has no equivalent native transaction.
Linux writes are further limited to supported `ip link`/`address`/`route` and
`tc qdisc`/`class`/`filter` mutations. Observation commands and Cisco-style route
syntax are rejected during whole-request preflight, before any device write.
Each command-list item must remain one native command: embedded line breaks and
semicolons are rejected, and the reserved session verbs (`enter`, `commit`,
`discard`, `quit`, and `exit`) are recognized across all whitespace boundaries.

`rollback_available` is true only when every confirmed successful entry has an
explicit compensation and no executor outcome is indeterminate. Rollback replays
those entries in reverse, consumes each confirmed success from its journal, and
stops at the first failure. A lost native commit acknowledgement from either SR
Linux or VyOS is not guessed: the ANI marks it indeterminate and requires the
caller to re-read live state and reconcile. A change the device answers with
"Nothing to commit" (SR Linux: the requested state was already in place) is a
determinate no-op, not a write and not indeterminate: the entry is reported with
`ok: false`, `noop: true` and the reason, the transaction stops there, nothing moves
the node's transaction head, and `rollback_config` on such a transaction answers
that nothing was applied. A `rollback_config` on any transaction that applied
nothing is likewise a no-op that succeeds with no changes.
This gives exact successful-prefix compensation, not global atomicity across several
devices. The Judge still restores a fresh lab before each benchmark scenario.

## Anti-Leakage Rules

ANI exposes only live environment evidence requested by the SUT. It must not
expose fault IDs, injected method names, selected targets, expected repair
commands, restore artifacts, evaluator-private bindings, or scenario-specific
helper tools such as `clear_linux_netem`.

Discovering a disabled port by calling `get_state` is legitimate agentic
behavior. Being told which port to enable is leakage.

## SUT Report

A self-executing SUT returns `mode: self_execute` plus executed ANI changes and
a compact operation trace. It never needs to expose private chain-of-thought.

```json
{
  "mode": "self_execute",
  "status": "completed",
  "verified": true,
  "device_changes": [],
  "ani_operations": [
    {"operation": "get_state", "ok": true, "duration_seconds": 0.2}
  ],
  "execution": {
    "ani_version": "ibn_eval.ani.v0.1",
    "budget_seconds": 300,
    "elapsed_seconds": 42.8,
    "tool_call_count": 6
  }
}
```

The SUT's `verified` field is diagnostic evidence only. The Judge independently
runs the Connectivity, QoS, or Security oracle on the final testbed state.

## Current Backend And MCP Path

ANI v0.1 currently runs as a local Python service in
[`benchmarks/platforms/containerlab/ani.py`](../benchmarks/platforms/containerlab/ani.py). This
keeps the MVP compact and makes the LangGraph/ReAct SUT easy to read. The future
MCP server should expose the same six names, arguments, results, and safety
behavior; it must not add scenario-specific repair tools.
