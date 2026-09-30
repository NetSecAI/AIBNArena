# filtering.zone_policy_enforcement.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 195376173, run `langchain_agent-openai-qwen-qwen3-8-27b-filtering-zone_policy_enforcement-m1-fault-seed195376173-98e5c501d09a`, recorded 2026-09-30T00:57:17 UTC.

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
| termination | budget: execution budget exceeded (turn 32) |
| model calls | 32 (tool calls 40) |
| wall clock | 425 s of a 400 s budget |
| time in the model | 294 s |
| tokens in / out | 723625 / 3144 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 26 (26) |
| device mutations (successful) | 5 (4) |
| validations | 13 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 38 accepted of 40 tool calls |
| repeat_action_rate | 9 repeated of 40 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 26, 'validate': 9, 'apply_config': 5} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/filtering.zone_policy_enforcement.m1.json`](../../../records/filtering.zone_policy_enforcement.m1.json)
* trace: not recorded
* judge log: [`logs/judge/filtering.zone_policy_enforcement.m1.judge.log`](../../../logs/judge/filtering.zone_policy_enforcement.m1.judge.log)
