# qos.link_impairment.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 225485985, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-link_impairment-m1-fault-seed225485985-5d6e797bd6c6`, recorded 2026-09-29T14:12:05 UTC.

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
| model calls | 19 (tool calls 24) |
| wall clock | 166 s of a 400 s budget |
| time in the model | 101 s |
| tokens in / out | 359480 / 2119 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 19 (18) |
| device mutations (successful) | 3 (1) |
| validations | 6 |
| failed operations | 3 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> external1's eth1 carried a tc netem qdisc with delay 3.6s, producing ~3.5–5.3s per-hop RTT on external1→web1 (10.10.40.10) with 0% loss. Removed it with 'tc qdisc del dev eth1 root' on external1 (tx ani_tx_45ed88e3044db661). Post-fix public_success_criteria passed: external1→web1 0% loss, avg RTT 0.33ms (limit 10ms), and lab_connectivity across all endpoint pairs preserved.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 21 accepted of 24 tool calls |
| repeat_action_rate | 4 repeated of 24 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 19, 'validate': 2, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m1.json`](../../../records/qos.link_impairment.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m1.judge.log`](../../../logs/judge/qos.link_impairment.m1.judge.log)
