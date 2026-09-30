# qos.wan_shaping_policy_repair.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 338921343, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m2-fault-seed338921343-54f8f453b73f`, recorded 2026-09-29T13:20:09 UTC.

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
| termination | budget: execution budget exceeded (turn 42) |
| model calls | 42 (tool calls 33) |
| wall clock | 461 s of a 400 s budget |
| time in the model | 311 s |
| tokens in / out | 1155604 / 6700 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 10 (9) |
| device mutations (successful) | 20 (6) |
| validations | 7 |
| failed operations | 17 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 18 accepted of 33 tool calls |
| repeat_action_rate | 4 repeated of 33 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 10, 'apply_config': 20, 'validate': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m2.json`](../../../records/qos.wan_shaping_policy_repair.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m2.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m2.judge.log)
