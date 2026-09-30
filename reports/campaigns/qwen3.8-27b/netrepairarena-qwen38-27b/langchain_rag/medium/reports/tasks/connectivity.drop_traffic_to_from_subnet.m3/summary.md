# connectivity.drop_traffic_to_from_subnet.m3

**Verdict: not repaired.**

Intent wording `medium`, seed 240144286, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m3-fault-seed240144286-174d5b38f26e`, recorded 2026-09-29T11:18:20 UTC.

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
| termination | budget: execution budget exceeded (turn 45) |
| model calls | 45 (tool calls 56) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 288 s |
| tokens in / out | 1833552 / 3802 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 53 (44) |
| device mutations (successful) | 2 (0) |
| validations | 5 |
| failed operations | 12 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 45 accepted of 56 tool calls |
| repeat_action_rate | 2 repeated of 56 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 53, 'validate': 1, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m3.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m3.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m3.judge.log)
