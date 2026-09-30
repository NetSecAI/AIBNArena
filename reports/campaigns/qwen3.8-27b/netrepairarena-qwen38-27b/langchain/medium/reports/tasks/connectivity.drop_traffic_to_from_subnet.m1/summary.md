# connectivity.drop_traffic_to_from_subnet.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 450677787, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-drop_traffic_to_from_subnet-m1-fault-seed450677787-733b47a5b1b2`, recorded 2026-09-29T10:52:23 UTC.

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
| termination | budget: execution budget exceeded (turn 33) |
| model calls | 33 (tool calls 47) |
| wall clock | 401 s of a 400 s budget |
| time in the model | 228 s |
| tokens in / out | 1185789 / 3672 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 40 (36) |
| device mutations (successful) | 0 (0) |
| validations | 11 |
| failed operations | 9 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 43 accepted of 47 tool calls |
| repeat_action_rate | 6 repeated of 47 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 40, 'validate': 7} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.drop_traffic_to_from_subnet.m1.json`](../../../records/connectivity.drop_traffic_to_from_subnet.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.drop_traffic_to_from_subnet.m1.judge.log`](../../../logs/judge/connectivity.drop_traffic_to_from_subnet.m1.judge.log)
