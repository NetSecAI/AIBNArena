# connectivity.wrong_routing_table.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 713030911, run `langchain_agent-gpt-5-4-connectivity-wrong_routing_table-m2-fault-seed713030911-b392d5d51ca6`, recorded 2026-09-27T16:41:26 UTC.

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
| termination | own_conclusion: completed (turn 20) |
| model calls | 20 (tool calls 18) |
| wall clock | 134 s of a 400 s budget |
| time in the model | 34 s |
| tokens in / out | 256435 / 1217 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 14 (14) |
| device mutations (successful) | 2 (1) |
| validations | 6 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Observed only guest1<->web1 IPv4 connectivity failing in the initial validation, while other paths were healthy. Read host state and gateway interfaces, then found on leaf2 a more-specific static route 10.10.20.10/32 pointing to blackhole next-hop-group ibn-wrt-m2, overriding the normal 10.10.20.0/24 route to leaf1. Deleted that single route on leaf2. Re-validation passed the public success criteria, including successful guest1<->web1 and web1<->guest1 pings.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 17 accepted of 18 tool calls |
| repeat_action_rate | 2 repeated of 18 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'validate': 2, 'check_config': 14, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m2.json`](../../../records/connectivity.wrong_routing_table.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m2.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m2.judge.log)
