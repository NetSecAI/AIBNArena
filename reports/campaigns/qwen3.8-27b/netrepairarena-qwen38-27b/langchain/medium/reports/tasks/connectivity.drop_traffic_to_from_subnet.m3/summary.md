# connectivity.drop_traffic_to_from_subnet.m3

**Verdict: not repaired.**

Intent wording `medium`, seed 240144286, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m3-fault-seed240144286-c4246c67e11e`, recorded 2026-09-29T14:05:26 UTC.

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
| termination | budget: execution budget exceeded (turn 38) |
| model calls | 38 (tool calls 53) |
| wall clock | 410 s of a 400 s budget |
| time in the model | 296 s |
| tokens in / out | 1555984 / 4245 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 39 (35) |
| device mutations (successful) | 2 (2) |
| validations | 16 |
| failed operations | 15 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 48 accepted of 53 tool calls |
| repeat_action_rate | 13 repeated of 53 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 39, 'validate': 12, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m3.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m3.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m3.judge.log)
