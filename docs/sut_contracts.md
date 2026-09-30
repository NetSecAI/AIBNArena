# SUT Contract: A2A + ANI v0.1

[`benchmarks/run.py`](../benchmarks/run.py) is the Judge entry point. A benchmark
domain prepares a fresh ContainerLab, injects its private scenario, sends a
public task to the SUT over A2A, then independently verifies the final state.

```text
Judge -> A2A request -> SUT -> ANI v0.1 -> ContainerLab
Judge -> domain oracle -> correctness / safety / latency
```

## Active A2A Profile

```text
contract_version: ibn_eval.a2a.v1
expected_output_modes: [self_execute]
ani.version: ibn_eval.ani.v0.2
```

The only active repair mode is `self_execute`. The SUT owns all inspection and
configuration activity; the Judge never replays an action from its report.

## Request

The request contains only public data:

```json
{
  "scenario_id": "scn_<opaque-random-id>",
  "intent": "Restore the intended service outcome safely.",
  "constraints": {
    "avoid_destructive_commands": true,
    "preserve_existing_connectivity": true,
    "do_not_modify_unrelated_devices": true
  },
  "observation": {},
  "success_criteria": {"all_of": [{"type": "lab_connectivity"}]},
  "execution_budget": {"wall_clock_seconds": 300},
  "ani": {"version": "ibn_eval.ani.v0.2"}
}
```

The request never includes a fault ID, injection command, target binding,
expected repair, restore configuration, or private oracle data. `scenario_id`
is deliberately opaque.

`success_criteria` may express a public Connectivity, QoS, or Security SLO. It
is intent context, not a hint about how the fault was injected.

## ANI

The SUT may use only the generic ANI operations:

```text
get_topology
get_state
get_running_config
get_object
update_config
update_object
execute_validation
rollback_config
```

Their full semantics, safety and anti-leakage rules are documented in
[`ani_v0_1.md`](ani_v0_1.md). The current local implementation is MCP-compatible
but is not an MCP server yet.

## Response

```json
{
  "mode": "self_execute",
  "status": "completed",
  "verified": true,
  "device_changes": [
    {
      "transaction_id": "ani_tx_...",
      "operation": "update_config",
      "action": {
        "machine": "leaf2",
        "command": "set / interface ethernet-1/40 admin-state enable"
      },
      "result": {"ok": true, "safe": true},
      "verified_after_action": true
    }
  ],
  "ani_operations": [
    {"operation": "get_state", "ok": true, "duration_seconds": 0.2}
  ],
  "execution": {
    "ani_version": "ibn_eval.ani.v0.2",
    "budget_seconds": 300,
    "elapsed_seconds": 42.8,
    "tool_call_count": 6
  }
}
```

Valid statuses are `completed`, `failed`, and `timeout`. A failed or timed-out
report remains useful: the Judge records it and evaluates the final lab state.
The report must contain a `device_changes` list, possibly empty (`actions` in records
written before ANI v0.2, which `device_changes_of` still reads). It must not contain
private chain-of-thought.

## Plugging Another SUT

A future CrewAI, LangGraph, or custom agent needs only to:

1. expose an A2A agent card and accept the public JSON task;
2. implement the ANI v0.1 operations locally or through MCP;
3. respect `execution_budget.wall_clock_seconds`;
4. return the `self_execute` JSON report;
5. leave final scoring to the Judge.

The benchmark mechanism is independent of the SUT's internal framework.
