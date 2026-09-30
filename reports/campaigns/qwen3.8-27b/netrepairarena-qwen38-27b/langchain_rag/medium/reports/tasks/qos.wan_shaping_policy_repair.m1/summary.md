# qos.wan_shaping_policy_repair.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 401943731, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m1-fault-seed401943731-918619b0e87c`, recorded 2026-09-29T13:07:00 UTC.

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
| termination | own_conclusion: completed (turn 26) |
| model calls | 26 (tool calls 23) |
| wall clock | 293 s of a 400 s budget |
| time in the model | 153 s |
| tokens in / out | 473931 / 3781 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 13 (12) |
| device mutations (successful) | 8 (2) |
| validations | 6 |
| failed operations | 8 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> The business class on wan1's shaped WAN egress (eth2 HTB) was broken: baseline user1→external1 measured 0.002 Mbps under contention while all connectivity passed. Diagnosis showed the HTB root and best-effort class 1:20 (root default) were intact, but the business class 1:10 and its classifier were missing, so user1 (10.10.10.0/24) traffic fell to best-effort. I restored the guarantee in place on wan1/eth2: created class 1:10 (rate 9mbit, ceil 20mbit, prio 1) and added a u32 classifier matching ip src 10.10.10.0/24 into flowid 1:10. No other device was modified. Validation: lab_connectivity all passed, and observed_throughput under contention measured 8.607 Mbps (min 8.0, floor 6.8) — public_success_criteria passed.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 16 accepted of 23 tool calls |
| repeat_action_rate | 4 repeated of 23 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 13, 'validate': 2, 'apply_config': 8} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m1.json`](../../../records/qos.wan_shaping_policy_repair.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m1.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m1.judge.log)
