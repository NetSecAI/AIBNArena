# connectivity.drop_traffic_to_from_subnet.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 919492398, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m2-fault-seed919492398-fd067663c18c`, recorded 2026-09-29T11:03:44 UTC.

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
| model calls | 55 (tool calls 55) |
| wall clock | 401 s of a 400 s budget |
| time in the model | 205 s |
| tokens in / out | 2231107 / 3938 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 39 (32) |
| device mutations (successful) | 9 (7) |
| validations | 11 |
| failed operations | 16 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 46 accepted of 55 tool calls |
| repeat_action_rate | 7 repeated of 55 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 39, 'validate': 7, 'apply_config': 9} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m2.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m2.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m2.judge.log)
