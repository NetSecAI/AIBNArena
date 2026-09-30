# connectivity.disable_routing.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 455925721, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-disable_routing-m1-fault-seed455925721-a04a410f18cc`, recorded 2026-09-29T10:15:16 UTC.

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
| termination | own_conclusion: completed (turn 19) |
| model calls | 19 (tool calls 20) |
| wall clock | 175 s of a 400 s budget |
| time in the model | 96 s |
| tokens in / out | 311185 / 1821 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 15 (11) |
| device mutations (successful) | 3 (1) |
| validations | 6 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Root cause: leaf1's network-instance default was admin-state disabled, so its FIB had zero routes even though interfaces (10.10.10.1/24, 10.10.20.1/24, p2p /31s) were up. Set network-instance default admin-state enable on leaf1 via update_object (transaction ani_tx_763b3d4b755570c3). Public success criteria validation now passes: all 10 connectivity pairs succeed (user1↔web1, user1↔app1, guest1↔web1, plus previously healthy finance1↔app1 and web1↔external1 still passing).

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 14 accepted of 20 tool calls |
| repeat_action_rate | 1 repeated of 20 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 15, 'validate': 2, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m1.json`](../../../records/connectivity.disable_routing.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m1.judge.log`](../../../logs/judge/connectivity.disable_routing.m1.judge.log)
