# connectivity.drop_traffic_to_from_subnet.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 919492398, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m2-fault-seed919492398-8084e56fe338`, recorded 2026-09-29T11:07:16 UTC.

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
| termination | budget: execution budget exceeded (turn 52) |
| model calls | 52 (tool calls 80) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 283 s |
| tokens in / out | 2456570 / 6666 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 69 (63) |
| device mutations (successful) | 5 (4) |
| validations | 10 |
| failed operations | 13 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 72 accepted of 80 tool calls |
| repeat_action_rate | 17 repeated of 80 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 69, 'validate': 6, 'apply_config': 5} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m2.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m2.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m2.judge.log)
