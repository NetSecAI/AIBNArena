# qos.wan_shaping_policy_repair.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 401943731, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m1-fault-seed401943731-0680d28abdf4`, recorded 2026-09-29T13:03:36 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | budget: execution budget exceeded (turn 51) |
| model calls | 51 (tool calls 57) |
| wall clock | 465 s of a 400 s budget |
| time in the model | 342 s |
| tokens in / out | 1527421 / 7469 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 30 (27) |
| device mutations (successful) | 25 (7) |
| validations | 6 |
| failed operations | 22 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 36 accepted of 57 tool calls |
| repeat_action_rate | 12 repeated of 57 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 30, 'apply_config': 25, 'validate': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m1.json`](../../../records/qos.wan_shaping_policy_repair.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m1.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m1.judge.log)
