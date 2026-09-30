# qos.wan_shaping_policy_repair.m4

**Verdict: repaired by the oracle's reading, but the subject did not conclude.**

Intent wording `medium`, seed 411389148, run `langchain_rag_agent-gpt-5-4-qos-wan_shaping_policy_repair-m4-fault-seed411389148-99b68c518750`, recorded 2026-09-29T07:37:24 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | yes |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | yes |
| repair score | 1.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: failed (turn 24) |
| model calls | 24 (tool calls 21) |
| wall clock | 122 s of a 400 s budget |
| time in the model | 60 s |
| tokens in / out | 337752 / 1793 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 10 (10) |
| device mutations (successful) | 10 (2) |
| validations | 5 |
| failed operations | 9 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Validated the task and observed lab connectivity passing but protected throughput failing: user1 to external1 measured 1.913 Mbps under contention, below the required 8 Mbps floor. Read topology and confirmed the bottleneck-facing node is wan1 toward external1 on eth2. Read wan1 and found an HTB root qdisc on eth2, then removed that broken root and successfully recreated only the root qdisc and parent class on wan1 eth2. Attempts to safely add the child guaranteed and best-effort classes through the available ANI QoS object interface failed with backend tc grammar/creation limitations (No such file or directory / unsupported root handle semantics), so a complete verified hierarchy and classifier restoration could not be finished within the tool’s supported safe operations. Public success criteria were not revalidated as passed.

Subject error: Validated the task and observed lab connectivity passing but protected throughput failing: user1 to external1 measured 1.913 Mbps under contention, below the required 8 Mbps floor. Read topology and confirmed the bottleneck-facing node is wan1 toward external1 on eth2. Read wan1 and found an HTB root qdisc on eth2, then removed that broken root and successfully recreated only the root qdisc and pa

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 13 accepted of 21 tool calls |
| repeat_action_rate | 3 repeated of 21 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 10, 'validate': 1, 'apply_config': 10} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.wan_shaping_policy_repair.m4.json`](../../../records/qos.wan_shaping_policy_repair.m4.json)
* trace: not recorded
* judge log: [`logs/judge/qos.wan_shaping_policy_repair.m4.judge.log`](../../../logs/judge/qos.wan_shaping_policy_repair.m4.judge.log)
