# qos.link_impairment.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 225485985, run `langchain_agent-gpt-5-4-qos-link_impairment-m1-fault-seed225485985-d8a8ee876362`, recorded 2026-09-27T17:08:37 UTC.

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
| termination | own_conclusion: failed (turn 11) |
| model calls | 11 (tool calls 10) |
| wall clock | 62 s of a 400 s budget |
| time in the model | 20 s |
| tokens in / out | 84572 / 647 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 8 (8) |
| device mutations (successful) | 1 (0) |
| validations | 5 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Validation found all internal lab connectivity healthy, but external1->web1 had 33.3% loss and 3565 ms average RTT, and web1<->external1 also failed in the lab-connectivity checks. Reads on leaf2 showed correct L3 interfaces and default-network-instance attachment for 10.10.40.0/24 and 203.0.113.0/24. Reads on external1 showed a netem qdisc on eth1 with 3.6 s delay, matching the symptom. A safe repair could not be completed because update_config rejected the needed Linux qdisc command as unsupported grammar.

Subject error: Validation found all internal lab connectivity healthy, but external1->web1 had 33.3% loss and 3565 ms average RTT, and web1<->external1 also failed in the lab-connectivity checks. Reads on leaf2 showed correct L3 interfaces and default-network-instance attachment for 10.10.40.0/24 and 203.0.113.0/24. Reads on external1 showed a netem qdisc on eth1 with 3.6 s delay, matching the symptom. A safe re

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 9 accepted of 10 tool calls |
| repeat_action_rate | 0 repeated of 10 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 8, 'validate': 1, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m1.json`](../../../records/qos.link_impairment.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m1.judge.log`](../../../logs/judge/qos.link_impairment.m1.judge.log)
