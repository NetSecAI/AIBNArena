# connectivity.wrong_routing_table.m2

**Verdict: repaired and concluded.**

Intent wording `medium`, seed 713030911, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-connectivity-wrong_routing_table-m2-fault-seed713030911-40740a6fc130`, recorded 2026-09-29T12:23:28 UTC.

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
| termination | own_conclusion: completed (turn 50) |
| model calls | 50 (tool calls 52) |
| wall clock | 339 s of a 400 s budget |
| time in the model | 187 s |
| tokens in / out | 1622610 / 4413 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 37 (36) |
| device mutations (successful) | 3 (1) |
| validations | 16 |
| failed operations | 11 |
| unsafe operations | 0 |

## The subject's conclusion

Status `completed`.

> Failing pairs were guest1(10.10.20.10)↔web1(10.10.40.10). Diagnosed that leaf2 had a static host route 10.10.20.10/32 pointing to a blackhole next-hop group (ibn-wrt-m2), silently dropping return traffic toward guest1. Removed that route on leaf2. All 10 lab_connectivity checks now pass with 0% loss, including the previously broken guest1↔web1 pair; no other pairs were affected.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 48 accepted of 52 tool calls |
| repeat_action_rate | 8 repeated of 52 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: yes |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 37, 'validate': 12, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/connectivity.wrong_routing_table.m2.json`](../../../records/connectivity.wrong_routing_table.m2.json)
* trace: not recorded
* judge log: [`logs/judge/connectivity.wrong_routing_table.m2.judge.log`](../../../logs/judge/connectivity.wrong_routing_table.m2.judge.log)
