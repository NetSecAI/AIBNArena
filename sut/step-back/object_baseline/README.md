# object_baseline

[`sut/langchain_agent_v2`](../../langchain_agent_v2/) handed the lab's topology, and reading the
devices through `get_object` only. `ObjectBaselineAgent` ([agent.py](agent.py)) is a subclass of
`LangChainAgentV2`, so a defect fixed there is fixed here.

| `langchain_agent_v2` | here |
|---|---|
| the first user message is the task | the task, then a second user message with the topology, read by the subject (below) |
| 9 tools, among them `get_state` and `get_running_config` | 7 tools: `get_topology_simple`, `get_object`, `update_config`, `update_object`, `execute_validation`, `rollback_config`, `submit_result`, each with v2's text and schema |

The prompt is v2's word for word, `# Configuration compliance` included. Until 1.1.0
(2026-11-21) the step-back prompts left that section out, while every other subject they were
compared with had it; Hugo had it added to every repair prompt. Episodes before 1.1.0 do not
compare with later ones.

Everything else is v2's:
- the ten most recent reads kept whole;
- the diagnosis turn of an episode with no conclusion;
- the record.

## The topology, then the model's own reads

Before the model's first call, the `BeforeFirstTurn` middleware makes one ANI read on the subject's
behalf, `get_topology`. It hands the model the answer `get_topology_simple` would give: the nodes
with their kind, and the links between their interfaces. The message has one line saying what it
is, then that answer as JSON. It is a user message, so the rule on older reads never turns it into
a stub. If the read fails, the message says so and the episode goes on.

From there the model reads the devices itself, with `get_object`, one object at a time. It has no
whole view of a device. A `get_running_config` or `get_state` call it writes anyway is refused
before any tool runs and filed as not dispatched (`FileRefusedCalls`). No tool's text and no line of
the prompt names either one, and a test holds this.

Since ANI v0.9 every element of a VyOS or Arista EOS configuration belongs to some object:
- `firewall_group`, `nat`, `service`, `system`, `vlan` and `config_section` beside the earlier kinds;
- an interface or a route carries its configuration lines beside its status view.

On campus_small, checked on a live lab on 2026-11-17, 192 `get_object` answers held every element of
the 15 nodes' configurations ([docs/ani_v0_1.md](../../../docs/ani_v0_1.md)). So everything
dumb_baseline is handed, this model can read.

## The record

The topology read is an `ani_operations` row with `source: topology_before_first_turn`, which the
judge does not count. `execution`'s counts (`tool_call_count`, `reads`) are the model's calls only.

`execution.topology_before_first_turn` holds:
- `ok`;
- the numbers of `nodes` and `links`;
- the message's size (`chars`);
- how long the read took (`seconds`).

## Running it

The path has a hyphen (`sut/step-back`), so no `import` statement can name this package. Load it
with `importlib.import_module("sut.step-back.object_baseline")`, or start the server as
`python -m sut.step-back.object_baseline.a2a_server`.

```bash
./sut/step-back/object_baseline/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

It listens on port 8016. Settings come from the command line, then `OBJECT_BASELINE_*`, then
`IBN_SUT_*` and `LLM_*`.
