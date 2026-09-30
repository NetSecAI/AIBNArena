# qos.wan_shaping_policy_repair.m1

**Verdict: not repaired.**

Intent wording `medium`, seed 401943731, run `langchain_rag_agent-gpt-5-4-qos-wan_shaping_policy_repair-m1-fault-seed401943731-0f8fecdfc9bc`, recorded 2026-09-29T07:16:42 UTC.

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
| termination | own_conclusion: failed (turn 15) |
| model calls | 15 (tool calls 12) |
| wall clock | 114 s of a 400 s budget |
| time in the model | 34 s |
| tokens in / out | 176366 / 737 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 10 (10) |
| device mutations (successful) | 1 (0) |
| validations | 5 |
| failed operations | 2 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Validated the task and observed all lab connectivity passing, but the public throughput criterion failed under contention. On wan1, the bottleneck uplink is eth2 and it does have an HTB root qdisc, confirming shaping is involved. However, the available read interfaces only exposed the root qdisc and not the live HTB class/filter hierarchy needed to safely identify whether the broken element is the business class rate, the default class, or the classifier. A direct attempt to inspect tc classes/filters via guarded native commands was blocked by ANI safety preflight, so no safe, evidence-based repair could be applied without risking unrelated connectivity.

Subject error: Validated the task and observed all lab connectivity passing, but the public throughput criterion failed under contention. On wan1, the bottleneck uplink is eth2 and it does have an HTB root qdisc, confirming shaping is involved. However, the available read interfaces only exposed the root qdisc and not the live HTB class/filter hierarchy needed to safely identify whether the broken element is the

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 11 accepted of 12 tool calls |
| repeat_action_rate | 1 repeated of 12 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 10, 'validate': 1, 'apply_config': 1} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m1.json`](../../../records/qos.wan_shaping_policy_repair.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m1.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m1.judge.log)
