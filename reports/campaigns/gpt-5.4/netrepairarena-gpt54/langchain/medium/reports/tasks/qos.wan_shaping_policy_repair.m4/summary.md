# qos.wan_shaping_policy_repair.m4

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 411389148, run `langchain_agent-gpt-5-4-qos-wan_shaping_policy_repair-m4-fault-seed411389148-de41fe2e1e7a`, recorded 2026-09-27T17:44:01 UTC.

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
| termination | own_conclusion: completed (turn 15) |
| model calls | 15 (tool calls 14) |
| wall clock | 146 s of a 400 s budget |
| time in the model | 26 s |
| tokens in / out | 142739 / 993 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (7) |
| device mutations (successful) | 4 (1) |
| validations | 6 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Initial public validation showed lab connectivity passing but user1 to external1 throughput under contention at only 1.912 Mbps, below the 8 Mbps target. I identified the shaped WAN uplink on wan1 eth2 with an HTB qdisc and changed the implicated business class 1:10 guaranteed rate to 8mbit using tc, without modifying unrelated devices. Post-change public validation passed: lab connectivity remained intact and contended throughput from user1 to external1 increased to 7.648 Mbps, which satisfies the enforced success floor for the 8 Mbps objective.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 10 accepted of 14 tool calls |
| repeat_action_rate | 2 repeated of 14 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'validate': 2, 'check_config': 8, 'apply_config': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m4.json`](../../../records/qos.wan_shaping_policy_repair.m4.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m4.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m4.judge.log)
