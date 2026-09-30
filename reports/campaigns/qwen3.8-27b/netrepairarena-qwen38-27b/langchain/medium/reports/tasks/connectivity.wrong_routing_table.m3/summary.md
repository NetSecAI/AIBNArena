# connectivity.wrong_routing_table.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 588746215, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m3-fault-seed588746215-151c68e24bb0`, recorded 2026-09-29T12:17:09 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | yes |
| full success (oracle, converged, subject completed and verified) | yes |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 1.0 |
| subject's own status | completed (verified yes) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: completed (turn 16) |
| model calls | 16 (tool calls 15) |
| wall clock | 159 s of a 400 s budget |
| time in the model | 85 s |
| tokens in / out | 190467 / 1356 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 12 (9) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 4 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> guest1↔web1 was failing: leaf2's static route for 10.10.20.0/24 (guest VLAN on leaf1) pointed to next-hop-group ibn-wrt-m3 (192.0.2.1, unreachable). Changed that route's next-hop-group to to-leaf1 (10.255.0.4/10.255.0.6, the real leaf1 p2p), matching the pattern used by leaf2's other leaf1 routes. All lab_connectivity pings now pass 0% loss, including guest1↔web1.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 12 accepted of 15 tool calls |
| repeat_action_rate | 1 repeated of 15 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 12, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m3.json`](../../../records/connectivity.wrong_routing_table.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m3.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m3.judge.log)
