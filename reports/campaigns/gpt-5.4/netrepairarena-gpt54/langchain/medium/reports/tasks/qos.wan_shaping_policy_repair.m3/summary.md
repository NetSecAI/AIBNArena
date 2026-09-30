# qos.wan_shaping_policy_repair.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 759131574, run `langchain_agent-gpt-5-4-qos-wan_shaping_policy_repair-m3-fault-seed759131574-9a88d9f5c3c0`, recorded 2026-09-27T17:37:08 UTC.

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
| termination | own_conclusion: completed (turn 13) |
| model calls | 13 (tool calls 12) |
| wall clock | 92 s of a 400 s budget |
| time in the model | 24 s |
| tokens in / out | 92635 / 917 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 7 (6) |
| device mutations (successful) | 3 (1) |
| validations | 6 |
| failed operations | 3 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Located the affected path user1 -> leaf1 -> spine1/spine2 -> wan1 -> external1 and verified baseline connectivity was intact. Read wan1 and found the shaped WAN uplink on eth2 had only an HTB root qdisc visible, implicating missing or incorrect class policy on the actual bottleneck. Applied a narrow QoS repair only on wan1 eth2: rebuilt the HTB root and added an explicit business class with 8 mbit rate plus a filter matching traffic to 203.0.113.10. Public validation then passed: lab connectivity remained successful and the observed throughput test under contention passed at 65.612 Mbps, above the required minimum.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 9 accepted of 12 tool calls |
| repeat_action_rate | 1 repeated of 12 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 7, 'validate': 2, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m3.json`](../../../records/qos.wan_shaping_policy_repair.m3.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m3.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m3.judge.log)
