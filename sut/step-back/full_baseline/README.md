# full_baseline

[`sut/langchain_agent_v2`](../../langchain_agent_v2/) without `get_object`. The model reads the
network only through its whole views: `get_topology_simple`, `get_state` and `get_running_config`.
It has no read of one named object. `FullBaselineAgent` ([agent.py](agent.py)) is a subclass of
`LangChainAgentV2`, so a defect fixed there is fixed here.

| `langchain_agent_v2` | here |
|---|---|
| 9 tools, `get_object` among them | the same 8 tools without it, in the same order, each with v2's text and schema |
| the task's `tools` lists `get_object` | it does not: the list is the subject's own tools, whatever the judge declares |

The prompt is v2's word for word, `# Configuration compliance` included. Until 1.1.0
(2026-11-21) the step-back prompts left that section out, while every other subject they were
compared with had it; Hugo had it added to every repair prompt. Episodes before 1.1.0 do not
compare with later ones.

Everything else is v2's:
- the ten most recent reads kept whole;
- the diagnosis turn of an episode with no conclusion;
- the record.

No other tool's text and no line of the prompt names `get_object`, and a test holds this.

A `get_object` call the model writes anyway gets `create_agent`'s answer for a tool the surface
lacks (`Error: get_object is not a valid tool, try one of [...]`). It is filed in `ani_operations`
as not dispatched (`FileRefusedCalls`), counts as no read, and the episode goes on.

## Running it

The path has a hyphen (`sut/step-back`), so no `import` statement can name this package. Load it
with `importlib.import_module("sut.step-back.full_baseline")`, or start the server as
`python -m sut.step-back.full_baseline.a2a_server`.

```bash
./sut/step-back/full_baseline/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

It listens on port 8017. Settings come from the command line, then `FULL_BASELINE_*`, then
`IBN_SUT_*` and `LLM_*`.
