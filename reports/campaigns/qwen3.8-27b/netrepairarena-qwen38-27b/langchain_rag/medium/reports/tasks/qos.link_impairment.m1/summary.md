# qos.link_impairment.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 225485985, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-qos-link_impairment-m1-fault-seed225485985-665e9a961379`, recorded 2026-09-29T12:50:50 UTC.

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
| termination | own_conclusion: completed (turn 17) |
| model calls | 17 (tool calls 14) |
| wall clock | 161 s of a 400 s budget |
| time in the model | 80 s |
| tokens in / out | 217802 / 1637 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 10 (10) |
| device mutations (successful) | 2 (1) |
| validations | 6 |
| failed operations | 1 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> external1's eth1 carried a leftover `tc qdisc netem ... delay 3.6s` root egress policy, adding 3.6s to each egress packet and inflating the external1→web1 RTT to ~3980ms with 0% loss. Removed it with `tc qdisc del dev eth1 root` (transaction ani_tx_bd09048c27b0cd20). After the change, external1→web1 RTT is 0.291ms with 0% loss, and the full public_success_criteria check (lab_connectivity across all 10 pairs plus the observed_icmp_service endpoint) passes.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 13 accepted of 14 tool calls |
| repeat_action_rate | 4 repeated of 14 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 10, 'validate': 2, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m1.json`](../../../records/qos.link_impairment.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m1.judge.log`](../../../logs/judge/qos.link_impairment.m1.judge.log)
