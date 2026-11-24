# LangChain ANI baseline

This framework baseline uses `langchain.agents.create_agent` and `@tool` wrappers
in `ani_tools.py`. The model ends the episode with the ANI's `submit_result` tool
(ANI v0.4): a middleware stops the graph before the next model call once the ANI
accepted it, and the submitted status is kept as declared, with `verified` from the
last public validation. A JSON conclusion written as text still ends it, and a
`completed` there without a passing validation is recorded as `failed`. Since 2026-10-29 a
`submit_result(status="completed")` with no passing `public_success_criteria` validation since
the model's last write is sent back once, not dispatched, as the shared loop does: the
refusal stays in `ani_operations`, `execution.unverified_completion_pushback` records the
reason, and the next `completed` goes through. In E19 six of this subject's failures
concluded `completed` that way and ended there. Since 2026-10-30 a `submit_result(status="failed")`
made with more than half the budget left is sent back once too, as the shared loop has done
since E15 (`execution.early_failure_pushback`): in E20 three of the four scenarios
only this subject failed ended on such a `failed`, `disable_interface.m1` at 31 s with a summary
that read "will use icmp validation". Until
2026-10-07 the conclusion tool was langchain's `ToolStrategy(StructuredConclusion)`.
The underlying `ContainerLabANI` contains no LangChain imports.

A failed ANI request repeated with the same operation, arguments and error carries a note
from the third on (`repeated_request`, beside `data` and `context` in the answer): it has
failed that many times with the same answer and will fail the same way. Nothing ends. A
write the ANI applied starts the count again: the network the request asked about has
changed; a refused write or a no-op does not. A failed `execute_validation` has no error,
only its checks, so three failed validations of the same criteria count as one request
whatever failed each time. `execution.repeated_requests` names each note and each write that
reset the count. The rule is `sut.common.repeated_requests`, which the shared loop uses as
well. Until 2026-10-30 the third identical failure ended the episode instead
(`termination.cause: repeated_failed_requests`, `termination.repeated_request`,
`execution.stop_rule`): six episodes of E20 ended there, between 509 s and 1 129 s of 3 600,
while the subjects with no such rule ran on to their budget. Until 2026-10-27 the count
ran over the whole episode: it ended 18 of 33 episodes in E17, 17 of them on
`execute_validation`, and in 14 of those successful writes separated the three counted
validations.

`recursion_limit` is 10 000 since 2026-10-30. LangGraph counts four steps a turn with this
subject's two middlewares, so the 1 000 before it was 250 turns: once the stop above was gone it
would have ended an episode the budget had not (one E20 `wrong_routing_table.m3` episode
took 379 turns). The budget bounds the episode; the limit only guards
against a runaway graph.

`update_config`'s description gives one change per write platform (`UPDATE_CONFIG_EXAMPLES`
in `ani_tools.py`): SR Linux `set / …`, VyOS `set …`, Arista EOS configuration lines with
their mode line first and no `exit`, Linux one `ip` or `tc` command. A test holds each to
the platform's preflight, so an example the ANI would refuse fails the suite. The
description also says that reads go through the read tools: in E18, 15 of LangChain's 57
failed writes were `show` or `list` commands. `update_object` compiles nothing for Arista
EOS, and its description says so. Checked live on `campus_small`'s cEOS switches
(2026-10-27): reads by section and by line, a write and its rollback, one batch across two
interfaces and a VLAN with no `exit`, and each refusal the description warns of.

Since 2026-10-29 (pistes_e20 B17) the VyOS example adds a zone pair and its rule: words after
`set` or `delete`, never a `/` path, and the destination zone first (`set firewall zone <to>
from <from> firewall name <ruleset>`). In E19 LangChain wrote VyOS as paths (7 refusals in
`dhcp_provisioning.m2`) and zone pairs backwards (`zone_policy` m1, m2). `update_object`'s
description says to prefer it for a DHCP pool option, a zone pair and a traffic-control
hierarchy, that a zone's name is the destination zone, and what an `htb` must hold; the ANI
now refuses an `htb` with an unknown key, no `rate` or no default class, and a `delete_root`
alone on an HTB root (`docs/ani_v0_1.md`). In E19 `wan_shaping` m1 and m4 deleted the root,
then built one with no class from keys the ANI ignored, and the 10 Mbit circuit carried 180
to 195 Mbit/s.

A reply with neither a tool call nor a conclusion is sent back to the model with the shared
loop's nudge ("Use an ANI tool next, or conclude with submit_result or the required final JSON
status object."), every time, and the episode goes on (`_nudge_on_no_action`; each nudge in
`execution.no_action_nudges`). `create_agent` ends the graph on such a reply, and in E19 three
episodes ended there with no conclusion. A JSON conclusion written as text still ends it. A reply
whose text holds a tool call written out (`<tool_call>` markup the provider's parser did not
read) is told so first, as in the shared loop (`sut.common.messages.no_action_nudge`), and its
nudge records `unparsed_tool_call`.

## The method

Since 2026-10-30 the prompt's method is this one, in this subject's tool vocabulary: find
the devices on the path with `get_topology`; follow the packet on each of them (the interface it
leaves by, its address and subnet, the route that matches and its next hop, the filters at an
interface or at the device itself and the zones, the QoS or the service measured); compare a
device that lacks something with a working peer and build the repair on what the peer uses;
one hypothesis confirmed by a read; one narrow change, tested with its rollback rather than
after certainty; validate; on failure, compare the changed device's routing table with a peer's.
Until then it was five generic steps. Two sentences of the method that named a fault family
of the scenarios (a Linux host losing the routes of an interface brought down, two QoS classes
with inverted rates) were removed. The prompt also says that each node's out-of-band
management port is not part of the network under repair, and that
the task's intent, constraints and criteria are public. The RAG subject's prompt changed with
it.

ANI reads support node/path scoping, filtering and deterministic pagination.
Oversized responses are explicitly marked as truncated and stored as
content-addressed JSON artifacts under `reports/manual/results/artifacts`.

`offset`, `limit` and `filter_text` page over the devices of an answer: a device
whose text contains `filter_text` is kept whole. `get_state` and `get_running_config`
also select lines inside each device's text: `line_filter` keeps the lines that
contain a string, `line_offset` and `line_limit` page over them, and every kept line
carries its number in the whole text. A text read cut at `max_context_chars` says how
many lines each text has and names those three parameters. They are separate from
`limit` because LangChain already passes `limit`, usually 100, on most one-device reads
(859 of 1256 in E16 to E18). Until 2026-10-27 a text could only be read from its start,
so leaf1's injected ACL filter, listed after about 75 000 characters of default CPM
filters, never reached the model. The paging asked for is recorded as `paging` on the
operation. Both filters match an interface in either of Arista EOS's spellings: its
tables write Ethernet3 as Et3 (`show interfaces status`, `get_state`'s `interfaces`
view), its configuration in full, and until 2026-10-27 `line_filter: "Ethernet3"`
found nothing in that view.

```bash
./sut/langchain_agent/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

Pass `--sut-url http://127.0.0.1:8003` to a benchmark run. To execute the complete reproducible baseline campaign, use `experiments/run_langchain_baseline.sh` from the repository root.
