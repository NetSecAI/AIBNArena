# connectivity.disable_interface.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 647444944, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-disable_interface-m1-fault-seed647444944-59e15b233f54`, recorded 2026-09-29T10:03:07 UTC.

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
| termination | own_conclusion: completed (turn 11) |
| model calls | 11 (tool calls 11) |
| wall clock | 114 s of a 400 s budget |
| time in the model | 44 s |
| tokens in / out | 142262 / 909 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (5) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 4 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Failing pairs were external1↔web1. Cause: leaf2's internet-facing interface ethernet-1/58 (203.0.113.1/24) was admin-disabled (port-admin-disabled). Enabled it with `set / interface ethernet-1/58 admin-state enable`. Validation now passes: all 10 connectivity pairs succeed with 0% loss, including external1→web1 and web1→external1.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 8 accepted of 11 tool calls |
| repeat_action_rate | 1 repeated of 11 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 8, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_interface.m1.json`](../../../records/connectivity.disable_interface.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_interface.m1.judge.log`](../../../logs/judge/connectivity.disable_interface.m1.judge.log)
