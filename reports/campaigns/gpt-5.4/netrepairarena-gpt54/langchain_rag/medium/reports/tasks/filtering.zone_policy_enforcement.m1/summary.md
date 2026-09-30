# filtering.zone_policy_enforcement.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 195376173, run `langchain_rag_agent-gpt-5-4-filtering-zone_policy_enforcement-m1-fault-seed195376173-e5af2a5648af`, recorded 2026-09-29T09:26:48 UTC.

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
| termination | repeated_failed_requests: stopped after 3 identical failed execute_validation requests (turn 14) |
| model calls | 14 (tool calls 13) |
| wall clock | 211 s of a 400 s budget |
| time in the model | 30 s |
| tokens in / out | 177303 / 810 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (6) |
| device mutations (successful) | 2 (2) |
| validations | 7 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: stopped after 3 identical failed execute_validation requests

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 11 accepted of 13 tool calls |
| repeat_action_rate | 2 repeated of 13 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 8, 'validate': 3, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/filtering.zone_policy_enforcement.m1.json`](../../../records/filtering.zone_policy_enforcement.m1.json)
* trace: not recorded
* judge log: [`logs/judge/filtering.zone_policy_enforcement.m1.judge.log`](../../../logs/judge/filtering.zone_policy_enforcement.m1.judge.log)
