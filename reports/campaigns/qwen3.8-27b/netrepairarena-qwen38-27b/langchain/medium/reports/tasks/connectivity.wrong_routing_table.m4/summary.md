# connectivity.wrong_routing_table.m4

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 748462572, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m4-fault-seed748462572-ea6736e3e0d0`, recorded 2026-09-29T12:25:19 UTC.

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
| termination | own_conclusion: completed (turn 17) |
| model calls | 17 (tool calls 16) |
| wall clock | 251 s of a 400 s budget |
| time in the model | 117 s |
| tokens in / out | 279961 / 2825 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 9 (6) |
| device mutations (successful) | 4 (2) |
| validations | 7 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Root cause: leaf2 had a rogue static route 10.10.20.0/24 (guest VLAN) pointing to a single next-hop (10.255.0.4) via an ad-hoc group ibn-wrt-m4, added ~53s after the rest of the routes. This mis-forwarded guest traffic and broke guest1<->web1. Fix: replaced the route on leaf2 with the proper dual-path to-leaf1 next-hop group (matching 10.10.10.0/24). public_success_criteria now passes with 0% loss on all pairs.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 11 accepted of 16 tool calls |
| repeat_action_rate | 2 repeated of 16 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 9, 'validate': 3, 'apply_config': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m4.json`](../../../records/connectivity.wrong_routing_table.m4.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m4.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m4.judge.log)
