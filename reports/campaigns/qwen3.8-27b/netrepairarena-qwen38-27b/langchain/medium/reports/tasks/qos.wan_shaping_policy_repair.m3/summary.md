# qos.wan_shaping_policy_repair.m3

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 759131574, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-wan_shaping_policy_repair-m3-fault-seed759131574-3f7114f3ae69`, recorded 2026-09-29T13:26:10 UTC.

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
| termination | own_conclusion: completed (turn 35) |
| model calls | 35 (tool calls 34) |
| wall clock | 334 s of a 400 s budget |
| time in the model | 199 s |
| tokens in / out | 646616 / 4512 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 18 (14) |
| device mutations (successful) | 14 (4) |
| validations | 6 |
| failed operations | 15 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> On the WAN uplink (wan1 eth2), the HTB qdisc pointed its default to class 0x20 but no classes existed, collapsing business throughput to 0.007 Mbps. I rebuilt the hierarchy: root 1:1 (50M), default class 1:20 (40M), and business class 1:10 (8M guaranteed / 20M ceil). Validation now passes: lab_connectivity all reachable (0% loss) and user1->external1 observed_throughput = 18.847 Mbps under contention (>=8 Mbps), restoring the contractual guarantee without disrupting other traffic.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 20 accepted of 34 tool calls |
| repeat_action_rate | 6 repeated of 34 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 18, 'apply_config': 14, 'validate': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m3.json`](../../../records/qos.wan_shaping_policy_repair.m3.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m3.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m3.judge.log)
