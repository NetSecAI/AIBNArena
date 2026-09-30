# qos.link_impairment.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 600497036, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-link_impairment-m2-fault-seed600497036-aab24197e68e`, recorded 2026-09-29T12:50:02 UTC.

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
| termination | budget: execution budget exceeded (turn 43) |
| model calls | 43 (tool calls 51) |
| wall clock | 443 s of a 400 s budget |
| time in the model | 189 s |
| tokens in / out | 1601474 / 3201 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 44 (40) |
| device mutations (successful) | 0 (0) |
| validations | 11 |
| failed operations | 10 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 47 accepted of 51 tool calls |
| repeat_action_rate | 10 repeated of 51 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 44, 'validate': 7} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m2.json`](../../../records/qos.link_impairment.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m2.judge.log`](../../../logs/judge/qos.link_impairment.m2.judge.log)
