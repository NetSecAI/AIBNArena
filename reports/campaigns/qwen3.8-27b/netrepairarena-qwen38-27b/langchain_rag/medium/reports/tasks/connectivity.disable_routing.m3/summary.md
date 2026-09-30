# connectivity.disable_routing.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 681475440, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-disable_routing-m3-fault-seed681475440-a66796b179e1`, recorded 2026-09-29T10:39:13 UTC.

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
| termination | own_conclusion: completed (turn 7) |
| model calls | 7 (tool calls 7) |
| wall clock | 81 s of a 400 s budget |
| time in the model | 17 s |
| tokens in / out | 70458 / 617 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 4 (4) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 1 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> user1's routing table had 'prohibit default' instead of a unicast default route, blocking all its off-subnet traffic (user1→web1/app1 failed, and web1/app1→user1 replies were dropped). Replaced it with 'default via 10.10.10.1' (leaf1's address on 10.10.10.0/24, matching the pattern of healthy peer app1). Re-validation: all 10 endpoint pairs pass, lab_connectivity passed=true.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 7 accepted of 7 tool calls |
| repeat_action_rate | 1 repeated of 7 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 4, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.disable_routing.m3.json`](../../../records/connectivity.disable_routing.m3.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.disable_routing.m3.judge.log`](../../../logs/judge/connectivity.disable_routing.m3.judge.log)
