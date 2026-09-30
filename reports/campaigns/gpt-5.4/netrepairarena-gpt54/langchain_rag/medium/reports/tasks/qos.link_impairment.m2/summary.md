# qos.link_impairment.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 600497036, run `langchain_rag_agent-gpt-5-4-qos-link_impairment-m2-fault-seed600497036-c0e668e8b96f`, recorded 2026-09-29T07:09:41 UTC.

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
| model calls | 7 (tool calls 6) |
| wall clock | 82 s of a 400 s budget |
| time in the model | 14 s |
| tokens in / out | 51495 / 364 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 3 (3) |
| device mutations (successful) | 1 (1) |
| validations | 6 |
| failed operations | 1 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Observed public validation failure only in lab connectivity: app1->finance1 had 33.3% packet loss, while the required finance1->app1 path already had 0% loss and 0.247 ms average RTT. Read host qdisc state and found finance1 eth1 had an egress netem qdisc with corrupt 30%. Removed that qdisc on finance1 with 'tc qdisc del dev eth1 root'. Re-validation passed public success criteria: lab connectivity passed and finance1->app1 measured 0% loss with 0.269 ms average RTT.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 6 accepted of 6 tool calls |
| repeat_action_rate | 1 repeated of 6 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 3, 'validate': 2, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m2.json`](../../../records/qos.link_impairment.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m2.judge.log`](../../../logs/judge/qos.link_impairment.m2.judge.log)
