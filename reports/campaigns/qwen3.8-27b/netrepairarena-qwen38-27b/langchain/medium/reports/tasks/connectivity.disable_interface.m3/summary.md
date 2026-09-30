# connectivity.disable_interface.m3

**Verdict: not repaired.**

Intent wording `medium`, seed 61059369, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-disable_interface-m3-fault-seed61059369-0076a66b57d4`, recorded 2026-09-29T13:53:53 UTC.

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
| termination | budget: execution budget exceeded (turn 43) |
| model calls | 43 (tool calls 62) |
| wall clock | 454 s of a 400 s budget |
| time in the model | 202 s |
| tokens in / out | 2254825 / 4176 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 48 (44) |
| device mutations (successful) | 9 (6) |
| validations | 9 |
| failed operations | 12 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 55 accepted of 62 tool calls |
| repeat_action_rate | 4 repeated of 62 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 48, 'validate': 5, 'apply_config': 9} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_interface.m3.json`](../../../records/connectivity.disable_interface.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_interface.m3.judge.log`](../../../logs/judge/connectivity.disable_interface.m3.judge.log)
