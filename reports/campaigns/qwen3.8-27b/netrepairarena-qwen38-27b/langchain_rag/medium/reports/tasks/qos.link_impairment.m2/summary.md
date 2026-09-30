# qos.link_impairment.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 600497036, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-link_impairment-m2-fault-seed600497036-5f91581781be`, recorded 2026-09-29T12:56:50 UTC.

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
| termination | own_conclusion: completed (turn 16) |
| model calls | 16 (tool calls 13) |
| wall clock | 142 s of a 400 s budget |
| time in the model | 81 s |
| tokens in / out | 207903 / 1253 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (8) |
| device mutations (successful) | 2 (1) |
| validations | 7 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> The finance1→app1 path showed 10% ICMP loss with normal RTT. Diagnosis (PB-20/PB-06): finance1's eth1 egress carried a leftover netem qdisc (corrupt 30%), which corrupted a steady share of outgoing packets. A comparable path (web1→10.10.50.1) was clean. I removed the netem root qdisc via update_object (kind=qos, name=eth1, delete_root), confirmed eth1 returned to noqueue, and validated: finance1→10.10.50.10 now shows 0% loss, 0.362 ms avg RTT; the full public success criteria (lab_connectivity + observed_icmp_service) passed with no impact on unrelated paths.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 12 accepted of 13 tool calls |
| repeat_action_rate | 2 repeated of 13 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 8, 'validate': 3, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m2.json`](../../../records/qos.link_impairment.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m2.judge.log`](../../../logs/judge/qos.link_impairment.m2.judge.log)
