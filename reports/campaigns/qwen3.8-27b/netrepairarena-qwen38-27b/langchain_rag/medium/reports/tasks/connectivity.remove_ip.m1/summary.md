# connectivity.remove_ip.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 506567882, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-remove_ip-m1-fault-seed506567882-d87a7c60cb41`, recorded 2026-09-29T11:40:08 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | no |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | budget: execution budget exceeded (turn 55) |
| model calls | 55 (tool calls 62) |
| wall clock | 403 s of a 400 s budget |
| time in the model | 248 s |
| tokens in / out | 2125112 / 4347 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 52 (48) |
| device mutations (successful) | 0 (0) |
| validations | 14 |
| failed operations | 11 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 58 accepted of 62 tool calls |
| repeat_action_rate | 5 repeated of 62 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 52, 'validate': 10} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.remove_ip.m1.json`](../../../records/connectivity.remove_ip.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.remove_ip.m1.judge.log`](../../../logs/judge/connectivity.remove_ip.m1.judge.log)
