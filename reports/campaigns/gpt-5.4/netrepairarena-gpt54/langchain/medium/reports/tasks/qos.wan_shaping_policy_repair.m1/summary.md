# qos.wan_shaping_policy_repair.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 401943731, run `langchain_agent-gpt-5-4-qos-wan_shaping_policy_repair-m1-fault-seed401943731-f09c8eb48f9a`, recorded 2026-09-27T17:24:19 UTC.

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
| model calls | 23 (tool calls 21) |
| wall clock | 196 s of a 400 s budget |
| time in the model | 43 s |
| tokens in / out | 226895 / 1681 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 12 (12) |
| device mutations (successful) | 7 (1) |
| validations | 6 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Observed that lab connectivity already passed but the public throughput-under-contention criterion initially failed for user1->external1. On wan1, eth2 was the shaped WAN uplink and only showed a bare HTB root qdisc. I rebuilt the root QoS object on wan1 eth2 with HTB default 20. After that, public success criteria passed: lab connectivity remained successful and observed throughput under contention for user1 to external1 measured 74.978 Mbps, exceeding the 8 Mbps requirement.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 15 accepted of 21 tool calls |
| repeat_action_rate | 3 repeated of 21 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 12, 'validate': 2, 'apply_config': 7} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m1.json`](../../../records/qos.wan_shaping_policy_repair.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m1.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m1.judge.log)
