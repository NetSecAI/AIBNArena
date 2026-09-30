# connectivity.wrong_routing_table.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 588746215, run `langchain_agent-gpt-5-4-connectivity-wrong_routing_table-m3-fault-seed588746215-62b7a4101a32`, recorded 2026-09-27T16:47:41 UTC.

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
| termination | own_conclusion: completed (turn 23) |
| model calls | 23 (tool calls 22) |
| wall clock | 133 s of a 400 s budget |
| time in the model | 40 s |
| tokens in / out | 319231 / 1215 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 19 (18) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Observed only guest1<->web1 connectivity failing initially: guest1 to web1 had 100% loss and web1 to guest1 returned 'Destination Net Unreachable' from 10.10.40.1. Confirmed leaf1 had guest subnet 10.10.20.1/24 locally, while leaf2 lacked route 10.10.20.0/24. Added static route 10.10.20.0/24 on leaf2 via 10.255.0.4 toward leaf1. Re-validation passed public success criteria with all tested IPv4 paths succeeding.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 21 accepted of 22 tool calls |
| repeat_action_rate | 1 repeated of 22 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 19, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m3.json`](../../../records/connectivity.wrong_routing_table.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m3.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m3.judge.log)
