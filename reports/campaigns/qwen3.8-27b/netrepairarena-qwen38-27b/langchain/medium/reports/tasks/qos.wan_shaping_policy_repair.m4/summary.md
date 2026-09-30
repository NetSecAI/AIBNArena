# qos.wan_shaping_policy_repair.m4

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 411389148, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m4-fault-seed411389148-cc374eaea138`, recorded 2026-09-29T13:36:16 UTC.

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
| termination | own_conclusion: completed (turn 49) |
| model calls | 49 (tool calls 47) |
| wall clock | 326 s of a 400 s budget |
| time in the model | 190 s |
| tokens in / out | 1106799 / 3696 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 31 (29) |
| device mutations (successful) | 14 (6) |
| validations | 6 |
| failed operations | 11 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> wan1's internet uplink eth2 had a stale HTB root (refcnt 25) whose default class 1:20 swallowed traffic and prevented plain `tc qdisc replace` (RTNETLINK: Invalid argument). I rebuilt the qdisc via update_object: removed the root, recreated `htb default 20`, class 1:1 at rate/ceil 100mbit, and a u32 filter matching dst 203.0.113.10/32 -> class 1:1. No other devices were touched. public_success_criteria now passes: lab_connectivity all green, observed_throughput user1->external1 (203.0.113.10) under contention measured at 66.263 Mbps, above the 8 Mbps contractual guarantee.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 37 accepted of 47 tool calls |
| repeat_action_rate | 10 repeated of 47 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 31, 'apply_config': 14, 'validate': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m4.json`](../../../records/qos.wan_shaping_policy_repair.m4.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m4.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m4.judge.log)
