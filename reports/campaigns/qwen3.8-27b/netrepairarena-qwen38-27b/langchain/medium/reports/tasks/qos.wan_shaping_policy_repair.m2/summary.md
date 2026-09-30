# qos.wan_shaping_policy_repair.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 338921343, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m2-fault-seed338921343-8c69c0eece94`, recorded 2026-09-29T13:15:37 UTC.

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
| termination | budget: execution budget exceeded (turn 35) |
| model calls | 35 (tool calls 35) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 231 s |
| tokens in / out | 654011 / 5135 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 17 (15) |
| device mutations (successful) | 16 (4) |
| validations | 6 |
| failed operations | 16 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 21 accepted of 35 tool calls |
| repeat_action_rate | 5 repeated of 35 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 17, 'apply_config': 16, 'validate': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m2.json`](../../../records/qos.wan_shaping_policy_repair.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m2.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m2.judge.log)
