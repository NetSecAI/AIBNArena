# connectivity.wrong_routing_table.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 588746215, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m3-fault-seed588746215-e238da8f5654`, recorded 2026-09-29T12:29:44 UTC.

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
| wall clock | 144 s of a 400 s budget |
| time in the model | 66 s |
| tokens in / out | 339718 / 1202 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 12 (11) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Failing paths were guest1→web1 and web1→guest1 (Destination Net Unreachable from 10.10.40.1). Diagnosis: leaf2's static route 10.10.20.0/24 used next-hop-group ibn-wrt-m3 (192.0.2.1, unreachable) instead of to-leaf1 (10.255.0.4/10.255.0.6 via spines) like leaf2's other cross-leaf routes; the route was not in the FIB. Fixed by updating the route's next-hop-group to to-leaf1. Validation now shows all 10 connectivity pairs at 0% packet loss, including the previously failing guest1↔web1; all previously healthy pairs remain healthy.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 14 accepted of 15 tool calls |
| repeat_action_rate | 2 repeated of 15 tool calls |
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
