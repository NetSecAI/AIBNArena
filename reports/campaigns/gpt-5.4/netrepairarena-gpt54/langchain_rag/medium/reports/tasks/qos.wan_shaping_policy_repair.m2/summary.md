# qos.wan_shaping_policy_repair.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 338921343, run `langchain_rag_agent-gpt-5-4-qos-wan_shaping_policy_repair-m2-fault-seed338921343-806f321fee25`, recorded 2026-09-29T07:24:03 UTC.

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
| model calls | 20 (tool calls 16) |
| wall clock | 184 s of a 400 s budget |
| time in the model | 55 s |
| tokens in / out | 322655 / 1325 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 7 (5) |
| device mutations (successful) | 7 (3) |
| validations | 6 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Validated that lab connectivity was intact but user1 to external1 throughput under contention was only 0.432 Mbps. Repaired QoS on wan1 eth2 by updating the HTB classes to 1:10 rate 8mbit ceil 20mbit prio 0 and 1:20 rate 12mbit ceil 20mbit prio 7, and added a classifier matching source 10.10.10.10/32 to flowid 1:10. Re-validation passed: lab connectivity remained good and contended throughput measured 7.649 Mbps, which passed the enforced success threshold.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 10 accepted of 16 tool calls |
| repeat_action_rate | 1 repeated of 16 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 7, 'validate': 2, 'apply_config': 7} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m2.json`](../../../records/qos.wan_shaping_policy_repair.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m2.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m2.judge.log)
