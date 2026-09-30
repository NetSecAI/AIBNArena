# qos.wan_shaping_policy_repair.m4

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 411389148, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m4-fault-seed411389148-169651572450`, recorded 2026-09-29T13:39:00 UTC.

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
| wall clock | 166 s of a 400 s budget |
| time in the model | 101 s |
| tokens in / out | 184128 / 2730 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 10 (10) |
| device mutations (successful) | 4 (2) |
| validations | 5 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Fixed swapped HTB class rates on wan1's WAN uplink (eth2). The business traffic class 1:10 (matching user1's 10.10.10.0/24 prefix) had rate 2Mbit instead of 8Mbit, while best-effort class 1:20 had 8Mbit instead of 2Mbit. Changed class 1:10 to rate 8Mbit ceil 10Mbit and class 1:20 to rate 2Mbit ceil 10Mbit. Validation confirmed all lab connectivity (0% loss on 14 paths) and observed throughput of 7.649 Mbps under contention, exceeding the enforced 6.8 Mbps floor for the 8 Mbps guarantee.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 13 accepted of 15 tool calls |
| repeat_action_rate | 2 repeated of 15 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 10, 'apply_config': 4, 'validate': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m4.json`](../../../records/qos.wan_shaping_policy_repair.m4.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m4.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m4.judge.log)
