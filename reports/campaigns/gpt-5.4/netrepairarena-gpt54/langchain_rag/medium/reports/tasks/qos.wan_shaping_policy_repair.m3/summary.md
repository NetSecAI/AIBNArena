# qos.wan_shaping_policy_repair.m3

**Verdict: not repaired.**

Intent wording `medium`, seed 759131574, run `langchain_rag_agent-gpt-5-4-qos-wan_shaping_policy_repair-m3-fault-seed759131574-a1fb7f9764d5`, recorded 2026-09-29T07:31:16 UTC.

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
| termination | own_conclusion: failed (turn 19) |
| model calls | 19 (tool calls 14) |
| wall clock | 134 s of a 400 s budget |
| time in the model | 39 s |
| tokens in / out | 308638 / 943 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 11 (11) |
| device mutations (successful) | 2 (0) |
| validations | 5 |
| failed operations | 3 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Public validation showed lab connectivity passed but the throughput criterion failed under contention. I confirmed the bottleneck path terminates at wan1 and that wan1 eth2 has an HTB root qdisc, implicating the WAN egress shaper. However, the available read interfaces did not expose the full tc class/filter hierarchy, and native update attempts for tc readback were rejected by ANI safety preflight as unsupported tc operations. Without evidence of the exact broken class or classifier, I could not make a safe narrow QoS repair on wan1 without risking unrelated connectivity. Final validation therefore remains failed for observed throughput.

Subject error: Public validation showed lab connectivity passed but the throughput criterion failed under contention. I confirmed the bottleneck path terminates at wan1 and that wan1 eth2 has an HTB root qdisc, implicating the WAN egress shaper. However, the available read interfaces did not expose the full tc class/filter hierarchy, and native update attempts for tc readback were rejected by ANI safety prefligh

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 12 accepted of 14 tool calls |
| repeat_action_rate | 1 repeated of 14 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'validate': 1, 'check_config': 11, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m3.json`](../../../records/qos.wan_shaping_policy_repair.m3.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m3.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m3.judge.log)
