# connectivity.drop_traffic_to_from_subnet.m4

**Verdict: not repaired.**

Intent wording `medium`, seed 465757805, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m4-fault-seed465757805-953a031f71ca`, recorded 2026-09-29T11:29:23 UTC.

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
| termination | budget: execution budget exceeded (turn 50) |
| model calls | 50 (tool calls 52) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 325 s |
| tokens in / out | 1906248 / 4945 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 46 (36) |
| device mutations (successful) | 3 (0) |
| validations | 7 |
| failed operations | 16 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 38 accepted of 52 tool calls |
| repeat_action_rate | 7 repeated of 52 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 46, 'validate': 3, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m4.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m4.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m4.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m4.judge.log)
