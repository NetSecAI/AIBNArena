# dumb_baseline

[`sut/langchain_agent_v2`](../../langchain_agent_v2/) handed the running configuration of every node
before its first turn. The model does not choose which configurations it reads: the subject reads
them all and puts them in front of the model. Everything else is `langchain_agent_v2`'s.
`DumbBaselineAgent` ([agent.py](agent.py)) is a subclass of `LangChainAgentV2`, so a defect fixed
there is fixed here.

| `langchain_agent_v2` | here |
|---|---|
| the first user message is the task | the task, then a second user message with every node's running configuration |

The prompt is v2's word for word, `# Configuration compliance` included. Until 1.1.0
(2026-11-21) the step-back prompts left that section out, while every other subject they were
compared with had it; Hugo had it added to every repair prompt. Episodes before 1.1.0 do not
compare with later ones.

## The configurations

Before the model's first call, the `BeforeFirstTurn` middleware (`before_agent`) makes two ANI
reads on the subject's behalf:

1. `get_topology` lists the nodes.
2. `get_running_config`, in text format, reads every node it listed.

The message starts with one line saying what it is. Each node then gets a section,
`### <node> (<kind>): <command>`, followed by what the node printed, whole. The text is not cut at
`max_context_chars`, and the read is not paged. A node whose read failed shows its error. If a whole
read failed, the message says so and the episode goes on.

- **A user message, not a tool result.** The rule on older reads (the ten most recent stay whole)
  turns only tool results into stubs, so the configurations stay whole for the whole episode, in
  every model call and in the diagnosis turn.
- **Read once.** They are not read again after a write: the message shows the network as it was
  when the episode started.
- **What the text read holds.** It is `get_running_config` text on each platform: `show` on VyOS,
  `show running-config` on Arista EOS, `ip address show` and `ip route show` on Linux. A Linux node's
  `tc` hierarchy (the WAN shaper on `wan-edge`) is not in it. The model reads it with its tools
  (`get_state` view `qdisc`, `get_object` kind `qos`, or `get_running_config` format `json`).
- **Size.** Nothing bounds it. On campus_small it is about 50,000 characters (the judge's device
  snapshots of the 15 nodes), and every model call sends it.

## The record

The two reads are rows of `ani_operations` with `source: configurations_before_first_turn`. They
follow the convention of the reads a subject makes on its own behalf: the judge does not count
them (`benchmarks/core/sut_client.py`), and this subject's `execution` counts (`tool_call_count`,
`reads`) leave them out too. A failed read does not add to `failed_operations`. The ANI's call cap
does count them, since the ANI counts every call.

`execution.configurations_before_first_turn` records the nodes listed (`nodes`), those whose
configuration came back (`read`), the message's size (`chars`) and how long the reads took
(`seconds`).

## What it keeps

All of `langchain_agent_v2`:
- the same tools with the same text, `get_running_config` included, so the model can still read a
  configuration again;
- the ten most recent reads kept whole;
- `FileRefusedCalls`;
- the diagnosis turn of an episode with no conclusion;
- the report's keys;
- the A2A transport.

## Running it

The path has a hyphen (`sut/step-back`), so no `import` statement can name this package. Load it
with `importlib.import_module("sut.step-back.dumb_baseline")`, as its server and its tests do, or
start the server as `python -m sut.step-back.dumb_baseline.a2a_server`.

```bash
./sut/step-back/dumb_baseline/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

It listens on port 8015. Pass `--sut-url http://127.0.0.1:8015` to a benchmark run. Settings come
from the command line, then `DUMB_BASELINE_*`, then the repository-wide `IBN_SUT_*` and `LLM_*`.
