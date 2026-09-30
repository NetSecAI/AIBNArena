# connectivity.disable_interface.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 959412850, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-disable_interface-m2-fault-seed959412850-f25f0002ee2d`, recorded 2026-09-29T10:04:50 UTC.

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
| termination | own_conclusion: completed (turn 14) |
| model calls | 14 (tool calls 13) |
| wall clock | 177 s of a 400 s budget |
| time in the model | 51 s |
| tokens in / out | 179146 / 863 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (8) |
| device mutations (successful) | 2 (2) |
| validations | 7 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> users_vlan was down end-to-end: user1's eth1 was administratively down and had no default route. Enabled eth1 and added default via 10.10.10.1; leaf1 ethernet-1/10 then came up. Public success criteria now pass with 0% loss on all 10 connectivity checks.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 13 accepted of 13 tool calls |
| repeat_action_rate | 4 repeated of 13 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 8, 'validate': 3, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_interface.m2.json`](../../../records/connectivity.disable_interface.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_interface.m2.judge.log`](../../../logs/judge/connectivity.disable_interface.m2.judge.log)
