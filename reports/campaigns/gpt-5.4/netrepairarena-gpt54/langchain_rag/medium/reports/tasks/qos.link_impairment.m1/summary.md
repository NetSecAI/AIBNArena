# qos.link_impairment.m1

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 225485985, run `langchain_rag_agent-gpt-5-4-qos-link_impairment-m1-fault-seed225485985-ad69d8f589db`, recorded 2026-09-29T07:04:39 UTC.

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
| termination | own_conclusion: completed (turn 18) |
| model calls | 18 (tool calls 17) |
| wall clock | 115 s of a 400 s budget |
| time in the model | 33 s |
| tokens in / out | 199421 / 985 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 13 (12) |
| device mutations (successful) | 2 (1) |
| validations | 6 |
| failed operations | 3 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Observed before change that only the external1<->web1 path was degraded: public validation showed 33.3333% packet loss and 7146.192 ms average RTT from external1 to 10.10.40.10, while other lab connectivity checks passed. Read external1 state/config and found eth1 up with qdisc netem applied, implicating a leftover impairment on the affected path. Removed the root qdisc on external1 eth1 via update_object(qos delete_root). Re-validation passed the full public success criteria: lab connectivity passed, and external1->web1 measured 0% packet loss with 0.287 ms average RTT.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 15 accepted of 17 tool calls |
| repeat_action_rate | 2 repeated of 17 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 13, 'validate': 2, 'apply_config': 2} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m1.json`](../../../records/qos.link_impairment.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m1.judge.log`](../../../logs/judge/qos.link_impairment.m1.judge.log)
