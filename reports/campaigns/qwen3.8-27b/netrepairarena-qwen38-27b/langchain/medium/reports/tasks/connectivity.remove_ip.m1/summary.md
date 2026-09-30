# connectivity.remove_ip.m1

**Verdict: repaired by the oracle's reading, but the subject did not conclude.**

Intent wording `medium`, seed 506567882, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-remove_ip-m1-fault-seed506567882-a3b3661d7779`, recorded 2026-09-29T11:29:47 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | yes |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 1.0 |
| subject's own status | failed (verified yes) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | budget: execution budget exceeded (turn 29) |
| model calls | 29 (tool calls 41) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 249 s |
| tokens in / out | 800634 / 5551 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 35 (30) |
| device mutations (successful) | 1 (1) |
| validations | 9 |
| failed operations | 9 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 35 accepted of 41 tool calls |
| repeat_action_rate | 4 repeated of 41 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 35, 'validate': 5, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.remove_ip.m1.json`](../../../records/connectivity.remove_ip.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.remove_ip.m1.judge.log`](../../../logs/judge/connectivity.remove_ip.m1.judge.log)
