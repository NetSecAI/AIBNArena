# qos.wan_shaping_policy_repair.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 338921343, run `langchain_agent-gpt-5-4-qos-wan_shaping_policy_repair-m2-fault-seed338921343-7a11743dddf5`, recorded 2026-09-27T17:30:43 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: failed (turn 12) |
| model calls | 12 (tool calls 11) |
| wall clock | 91 s of a 400 s budget |
| time in the model | 23 s |
| tokens in / out | 70475 / 1021 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 5 (5) |
| device mutations (successful) | 4 (0) |
| validations | 6 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Observed connectivity from user1 to external1 and broader lab connectivity were intact, but public validation still failed the throughput criterion: user1->external1 under contention measured 0.432 Mbps versus an enforced floor of 6.8 Mbps for the 8 Mbps objective. On wan1 I confirmed only an HTB root qdisc on eth2, implicating WAN QoS, but safe ANI reads did not expose class/filter details and attempted scoped QoS repair via update_object was rejected by backend tc grammar validation before any change was applied. No device configuration was changed; no safe repair could be completed with the available ANI operations.

Subject error: Observed connectivity from user1 to external1 and broader lab connectivity were intact, but public validation still failed the throughput criterion: user1->external1 under contention measured 0.432 Mbps versus an enforced floor of 6.8 Mbps for the 8 Mbps objective. On wan1 I confirmed only an HTB root qdisc on eth2, implicating WAN QoS, but safe ANI reads did not expose class/filter details and at

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 7 accepted of 11 tool calls |
| repeat_action_rate | 0 repeated of 11 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 5, 'validate': 2, 'apply_config': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m2.json`](../../../records/qos.wan_shaping_policy_repair.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m2.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m2.judge.log)
