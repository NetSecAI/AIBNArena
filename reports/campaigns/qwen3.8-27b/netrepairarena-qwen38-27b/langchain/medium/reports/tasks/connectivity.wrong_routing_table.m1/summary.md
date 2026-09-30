# connectivity.wrong_routing_table.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 684778202, run `langchain_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m1-fault-seed684778202-c048f88b2748`, recorded 2026-09-29T12:03:18 UTC.

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
| model calls | 19 (tool calls 27) |
| wall clock | 173 s of a 400 s budget |
| time in the model | 95 s |
| tokens in / out | 327900 / 3380 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 23 (22) |
| device mutations (successful) | 2 (1) |
| validations | 6 |
| failed operations | 3 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Root cause: leaf2's static route to user1's subnet 10.10.10.0/24 was poisoned with next-hop-group ibn-wrt-m1 (nexthop 10.10.50.10 = app1, a host in leaf2's own local 10.10.50.0/24), so return traffic was sent directly to app1 which issued ICMP network-redirects — breaking user1<->web1 and user1<->app1 while the forward path stayed up. Fix: on leaf2, retargeted route 10.10.10.0/24 to the existing working next-hop-group to-leaf1 (10.255.0.4/10.255.0.6 -> spine1/spine2 -> leaf1), matching the pattern of the other leaf1 subnets. No other device or healthy path was modified. Validation: public_success_criteria lab_connectivity passed — all 10 ping checks show 0% loss (previously 4 checks failed with 100% loss/redirects).

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 25 accepted of 27 tool calls |
| repeat_action_rate | 1 repeated of 27 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 23, 'validate': 2, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m1.json`](../../../records/connectivity.wrong_routing_table.m1.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m1.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m1.judge.log)
