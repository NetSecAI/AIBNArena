# filtering.zone_policy_enforcement.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 195376173, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-filtering-zone_policy_enforcement-m1-fault-seed195376173-ea2d11a542a6`, recorded 2026-09-29T09:36:34 UTC.

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
| termination | budget: execution budget exceeded (turn 19) |
| model calls | 19 (tool calls 22) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 205 s |
| tokens in / out | 335739 / 2671 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 16 (15) |
| device mutations (successful) | 0 (0) |
| validations | 10 |
| failed operations | 6 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 19 accepted of 22 tool calls |
| repeat_action_rate | 1 repeated of 22 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 16, 'validate': 6} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/filtering.zone_policy_enforcement.m1.json`](../../../records/filtering.zone_policy_enforcement.m1.json)
* trace: not recorded
* judge log: [`logs/judge/filtering.zone_policy_enforcement.m1.judge.log`](../../../logs/judge/filtering.zone_policy_enforcement.m1.judge.log)
