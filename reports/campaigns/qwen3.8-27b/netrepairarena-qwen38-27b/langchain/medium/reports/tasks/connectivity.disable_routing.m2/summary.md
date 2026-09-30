# connectivity.disable_routing.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 568447271, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-disable_routing-m2-fault-seed568447271-f1f4f9aee5ae`, recorded 2026-09-29T10:26:36 UTC.

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
| termination | budget: execution budget exceeded (turn 40) |
| model calls | 40 (tool calls 40) |
| wall clock | 413 s of a 400 s budget |
| time in the model | 192 s |
| tokens in / out | 1345843 / 5458 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 26 (26) |
| device mutations (successful) | 6 (3) |
| validations | 12 |
| failed operations | 11 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 37 accepted of 40 tool calls |
| repeat_action_rate | 2 repeated of 40 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 26, 'validate': 8, 'apply_config': 6} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m2.json`](../../../records/connectivity.disable_routing.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m2.judge.log`](../../../logs/judge/connectivity.disable_routing.m2.judge.log)
