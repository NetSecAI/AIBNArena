# connectivity.disable_routing.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 568447271, run `langchain_rag_agent-gpt-5-4-connectivity-disable_routing-m2-fault-seed568447271-82191a57a35e`, recorded 2026-09-29T05:17:19 UTC.

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
| termination | budget: execution budget exceeded (turn 88) |
| model calls | 88 (tool calls 22) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 310 s |
| tokens in / out | 2576632 / 27484 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 19 (17) |
| device mutations (successful) | 2 (0) |
| validations | 5 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 18 accepted of 22 tool calls |
| repeat_action_rate | 0 repeated of 22 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'validate': 1, 'check_config': 19, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m2.json`](../../../records/connectivity.disable_routing.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m2.judge.log`](../../../logs/judge/connectivity.disable_routing.m2.judge.log)
