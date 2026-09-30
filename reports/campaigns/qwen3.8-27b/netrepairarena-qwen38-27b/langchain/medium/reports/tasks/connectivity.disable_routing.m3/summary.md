# connectivity.disable_routing.m3

**Verdict: repaired by the oracle's reading, but the subject did not conclude.**

Intent wording `medium`, seed 681475440, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-disable_routing-m3-fault-seed681475440-b3355e89e95f`, recorded 2026-09-29T10:37:06 UTC.

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
| termination | budget: execution budget exceeded (turn 34) |
| model calls | 34 (tool calls 34) |
| wall clock | 408 s of a 400 s budget |
| time in the model | 293 s |
| tokens in / out | 940158 / 2499 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 29 (25) |
| device mutations (successful) | 3 (1) |
| validations | 6 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 28 accepted of 34 tool calls |
| repeat_action_rate | 3 repeated of 34 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 29, 'validate': 2, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m3.json`](../../../records/connectivity.disable_routing.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m3.judge.log`](../../../logs/judge/connectivity.disable_routing.m3.judge.log)
