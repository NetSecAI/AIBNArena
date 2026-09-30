# connectivity.disable_routing.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 455925721, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-disable_routing-m1-fault-seed455925721-e7fe6a00353d`, recorded 2026-09-29T10:23:17 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | yes |
| full success (oracle, converged, subject completed and verified) | yes |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 1.0 |
| subject's own status | completed (verified yes) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: completed (turn 9) |
| model calls | 9 (tool calls 8) |
| wall clock | 99 s of a 400 s budget |
| time in the model | 25 s |
| tokens in / out | 100866 / 707 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 5 (4) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Diagnosis: all failing pairs traversed leaf1, while leaf2-local paths worked; leaf1's default network-instance was admin-disabled (PB-14), leaving its FIB empty. Fix: set / network-instance default admin-state enable on leaf1 (transaction ani_tx_1a92940441f8d4d6). Validation: public_success_criteria lab_connectivity passed — all 10 endpoint pairs reach each other with 0% loss, previously-healthy paths unchanged.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 7 accepted of 8 tool calls |
| repeat_action_rate | 1 repeated of 8 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 5, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m1.json`](../../../records/connectivity.disable_routing.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m1.judge.log`](../../../logs/judge/connectivity.disable_routing.m1.judge.log)
