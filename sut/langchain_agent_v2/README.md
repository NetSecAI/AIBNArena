# LangChain ANI baseline v2

[`sut/langchain_agent`](../langchain_agent/) without what coaches the model or corrects it.
The baseline grew campaign after campaign out of its own failures: send-backs, notes, a
method, examples in its tool descriptions. Here the model keeps the reasoning, and an error
it makes stays its own, in the record.

This subject *is* the baseline, as `langchain_rag_agent` is: the same `create_agent` graph,
model binding, ANI, record and transport (`LangChainAgentV2` in [agent.py](agent.py)). A
defect fixed in the baseline is fixed here. What the model reads is all in
[ani_tools.py](ani_tools.py) and [prompts/default.txt](prompts/default.txt).

## What it takes away

| Baseline | Here |
|---|---|
| `submit_result(status="completed")` with no passing validation sent back once | reaches the ANI: `status: completed`, `verified: false` |
| `submit_result(status="failed")` sent back once with more than half the budget left | reaches the ANI: the episode ends |
| `repeated_request` note from the third identical failure | no note |
| `_nudge_on_no_action` on a reply with no tool call | the reply ends the graph, as `create_agent` does: `termination.cause: no_conclusion` |
| prompt: `# Method`, `# Examples`, `# Tools`, the end of `# Finish` | none of these: the prompt states the task, the network's rules and the end |
| tool descriptions with advice from campaigns, and a rule beside each platform's example | the ANI's own descriptions, five of them without their advice, each followed by example arguments with no rule (below) |
| `Ethernet3` matching `Et3` in `filter_text` and `line_filter` | a plain case-insensitive match |
| `read_further` in a cut text read, saying how to read on | the answer says it was cut (`truncated`, line counts); the parameters are in the schema |
| `get_topology` | `get_topology_simple` |
| a call `create_agent` refuses before any tool runs leaves no operation in the record | filed as an operation that was not dispatched (below) |

Five ANI descriptions are rewritten in `DESCRIPTIONS`, here only, since the ANI is shared:
`get_state` ("Use this before changing configuration"), `get_object` ("use it instead of a
whole view"), `update_config` ("Supply rollback_commands when a safe compensation is known"),
`submit_result` ("evaluate your changes first") and `execute_validation` ("the only validation
the completion gate accepts", which this subject does not have, and "do not repeat it idly").

Since ANI v0.10 (2026-11-17) the descriptions also say what each tool does, and give no advice:
- `get_state`: what each view holds;
- `get_running_config`: what the text read prints on each platform, and its section paths;
- `update_config`: what is committed together, in what order, and when `rollback_config` can
  undo it;
- `execute_validation`: what each field of an icmp check means.

`update_object`'s and `rollback_config`'s come from the ANI, which states the same facts.

Every description ends with example arguments (`EXAMPLES`): what a call looks like, not when
to make it. `update_config` shows the baseline's command forms per platform, without the rule
the baseline sets beside each and without its values, which are the labs' own. No node, port
or address in an example exists in a lab, so an example says nothing of a network under
repair. Tests hold this, every example to its tool's schema, and the object writes to the
ANI's compiler.

`get_topology_simple` makes one ANI `get_topology` read, counted by the ANI's cap and filed
under that operation as `category: read`. It takes no argument and returns
`nodes: [{name, kind}]` and `links: [{endpoint1: {node, interface}, endpoint2: …}]`. A segment
with two ports is written as a link, any other as `{endpoints: [...]}`: a VLAN or trunk shared
by several ports, or a single port whose peer the descriptor does not declare. Roles,
capabilities and segment names are left out. It answers whole: with no paging argument, a page
of nodes or a cut would hide part of the lab with no way to read it.

## What it keeps

- Every other ANI operation, `update_object` and `get_object` included: a capability, not a
  correction.
- `offset`, `limit`, `filter_text`, `line_filter`, `line_offset` and `line_limit`: past the cut
  at `max_context_chars`, a fact would otherwise be out of reach.
- `# Configuration compliance`, word for word: the network as designed, which the task is
  stated against. A test holds it to the baseline's.
- The out-of-band management port, a fact about the platform, and the writes' management-plane
  rule, which the ANI enforces.
- A validation result reaching the model whole, the budget, `ani_call_limit`,
  `min_retry_tokens`, `recursion_limit`, thinking, the trace and the report's keys.
- `ForcedToolChoiceRelaxed`, the JSON arguments decoded after vLLM's parser, and past reasoning
  sent back: transport, not advice.
- The ANI itself: its refusals and their wording are shared by every subject.

A JSON conclusion written as text is still read by the baseline's code; the prompt no longer
offers it, and none of `main01`'s 846 conclusions used it. A tool call written as text that the
provider's parser did not read ends the episode like any other reply with no tool call.

The report keeps every key of the baseline's: `early_failure_pushback` and
`unverified_completion_pushback` are always null, `no_action_nudges` empty, and
`repeated_requests` says `action: none`.

## A call refused before any tool runs

`create_agent`'s ToolNode answers two kinds of call with an error before any tool runs: arguments the
tool's argument model refuses (`Error invoking tool '<name>' with kwargs {...} with error: ...`), and a
name no tool has (`Error: <name> is not a valid tool, try one of [...]`). The tool's own code, which
files every other call, never runs, so the baseline's record holds no operation for them. Here
`FileRefusedCalls` ([agent.py](agent.py)) files each in `ani_operations` as the shared loop
(`sut/common/self_execute.py`) files it: `ok: false`, `dispatched: false`, the error the model read
and the arguments it sent. The judge counts it as a failed operation and as no read, write or
validation, and so do `execution.reads`, `mutations` and `validations`. A test plays each kind of
error.

## An episode with no conclusion

When an episode ends without a conclusion, on the budget or on a reply with no tool call, the model
is asked once, with no tool dispatched, what the fault was: the shared loop's own turn
(`_request_diagnosis`), so the diagnosis of this subject and of the shared loop's is measured alike.
It is recorded under `diagnosis` with `source: termination_turn`, and changes nothing else in the
result. It is not asked after a context overflow, since the transcript cannot be sent again.

## Older reads

Like the shared loop, this subject keeps the ten most recent read results whole. Before each model
call every older one becomes a stub naming its call, which the model can make again
(`KeepRecentReads` in [agent.py](agent.py), with `sut/common`'s `_elide_older_reads`). A stub never
comes back whole. The topology is never elided nor counted. Writes, validations and the model's own
messages are not touched, and a read is still cut at `max_context_chars`. The LangGraph state keeps
every result whole; only what goes to the model changes. The result records it under
`execution.context` (`keep_recent_reads`, `keep_recent_read_chars`, `elided_results` and an
`elided_older_reads` event per call that elided).

From 2026-11-15 to 2026-11-16 the v2 subjects counted in characters instead: the most recent
results stayed whole up to 60 000 characters together (`KEEP_RECENT_READ_CHARS` in
`sut/common/self_execute.py`), the answers to the model's last turn always whole. On main03's 27B
the ten reads held 31 000 characters for this subject, all it had read. main04 ran with the
character rule, and it was set back to ten reads after main04 at Hugo's request: one run per cell
cannot tell what the rule did to the counts. `IBN_SUT_KEEP_RECENT_READS=0` with
`IBN_SUT_KEEP_RECENT_READ_CHARS=60000` runs main04's rule for every subject of a run
(`LANGCHAIN_AGENT_V2_*` for this one alone); `*_KEEP_RECENT_READS=0` alone turns the stubs off, and
with both rules set a result stays whole only if each keeps it.

## Running it

```bash
./sut/langchain_agent_v2/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

It listens on port 8010. Pass `--sut-url http://127.0.0.1:8010` to a benchmark run. Settings
come from the command line, then `LANGCHAIN_AGENT_V2_*`, then the repository-wide `IBN_SUT_*`
and `LLM_*`.
