# qos.assured_bandwidth.m1

**Verdict: not repaired.**

Intent wording `high`, seed 38829221, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-assured_bandwidth-m1-fault-seed38829221-e30f856860eb`, recorded 2026-09-29T13:50:59 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | budget: execution budget exceeded (turn 50) |
| model calls | 50 (tool calls 48) |
| wall clock | 440 s of a 400 s budget |
| time in the model | 309 s |
| tokens in / out | 1199284 / 6399 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 17 (17) |
| device mutations (successful) | 28 (9) |
| validations | 7 |
| failed operations | 21 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 29 accepted of 48 tool calls |
| repeat_action_rate | 6 repeated of 48 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 17, 'apply_config': 28, 'validate': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.assured_bandwidth.m1.json`](../../../records/qos.assured_bandwidth.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.assured_bandwidth.m1.judge.log`](../../../logs/judge/qos.assured_bandwidth.m1.judge.log)
