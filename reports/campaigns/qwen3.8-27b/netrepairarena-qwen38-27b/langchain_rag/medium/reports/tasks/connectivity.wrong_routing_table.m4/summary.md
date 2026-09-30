# connectivity.wrong_routing_table.m4

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 748462572, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m4-fault-seed748462572-988d426775c7`, recorded 2026-09-29T12:37:46 UTC.

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
| termination | own_conclusion: completed (turn 25) |
| model calls | 25 (tool calls 30) |
| wall clock | 243 s of a 400 s budget |
| time in the model | 161 s |
| tokens in / out | 541256 / 3125 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 24 (19) |
| device mutations (successful) | 4 (1) |
| validations | 6 |
| failed operations | 9 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Failing paths were guest1<->web1 (guest subnet 10.10.20.0/24). leaf2's static route to 10.10.20.0/24 pointed to a bogus next-hop-group ibn-wrt-m4 (10.255.0.4) instead of to-leaf1 (10.255.0.4/10.255.0.6), blackholing all guest-subnet traffic. Corrected the route's next-hop-group to to-leaf1 on leaf2 (matching the working 10.10.10.0/24 and 10.10.99.0/24 routes). Public success criteria validation now passes with 0% loss on all 10 endpoint pairs.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 22 accepted of 30 tool calls |
| repeat_action_rate | 2 repeated of 30 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 24, 'validate': 2, 'apply_config': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m4.json`](../../../records/connectivity.wrong_routing_table.m4.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m4.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m4.judge.log)
