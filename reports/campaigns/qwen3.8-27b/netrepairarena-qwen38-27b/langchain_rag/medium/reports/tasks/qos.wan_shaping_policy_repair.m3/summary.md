# qos.wan_shaping_policy_repair.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 759131574, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m3-fault-seed759131574-f1a79d85d891`, recorded 2026-09-29T13:31:36 UTC.

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
| termination | own_conclusion: completed (turn 36) |
| model calls | 36 (tool calls 29) |
| wall clock | 365 s of a 400 s budget |
| time in the model | 239 s |
| tokens in / out | 993677 / 6138 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 17 (16) |
| device mutations (successful) | 9 (6) |
| validations | 7 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Repaired the WAN uplink shaping on wan1 (eth2, toward external1). Connectivity had always passed (0% loss), so the failure was the missing business-class guarantee under contention; the existing HTB root had broken class rates/classifier. Rebuilt a clean two-class HTB per playbook PB-21: root htb at 20 Mbit with default to best-effort (0x20); protected business class 1:10 rate 12 Mbit (>=8+header margin) ceil 20 Mbit prio 1, classified by source 10.10.10.0/24 (user VLAN); best-effort class 1:20 rate 8 Mbit ceil 20 Mbit prio 2 kept non-zero so other traffic stays connected. public_success_criteria validation returned passed=true: lab_connectivity all 0% loss, observed_throughput 11.473 Mbps under contention (>=8 Mbps floor 6.8). No other devices touched.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 25 accepted of 29 tool calls |
| repeat_action_rate | 7 repeated of 29 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 17, 'validate': 3, 'apply_config': 9} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m3.json`](../../../records/qos.wan_shaping_policy_repair.m3.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m3.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m3.judge.log)
