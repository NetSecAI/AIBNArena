# qos.assured_bandwidth.m1

**Verdict: not repaired.**

Intent wording `high`, seed 38829221, run `langchain_agent-gpt-5-4-qos-assured_bandwidth-m1-fault-seed38829221-fa1bb5ee2dd9`, recorded 2026-09-27T17:54:07 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | no (score 0.0) |
| non-regression | no |
| network converged to the reference | no |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: failed (turn 20) |
| model calls | 20 (tool calls 19) |
| wall clock | 281 s of a 400 s budget |
| time in the model | 39 s |
| tokens in / out | 261548 / 1867 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 11 (9) |
| device mutations (successful) | 6 (2) |
| validations | 6 |
| failed operations | 8 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Observed initial failure of public success criteria: lab connectivity to external1 was already broken for several nodes and user1 throughput measured near zero. Read path-related config and found no usable safe ANI-supported way to install selective Linux tc filters on wan1; preflight rejected the needed u32 matcher. Tested one alternate hypothesis on leaf2 external next hops, but validation worsened and showed user1 lost external reachability entirely, so I rolled that change back successfully. Final state: no persistent config changes remain from my actions, public success criteria still fail, and no safe repair for the required throughput guarantee was found with the allowed interfaces.

Subject error: Observed initial failure of public success criteria: lab connectivity to external1 was already broken for several nodes and user1 throughput measured near zero. Read path-related config and found no usable safe ANI-supported way to install selective Linux tc filters on wan1; preflight rejected the needed u32 matcher. Tested one alternate hypothesis on leaf2 external next hops, but validation worse

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 13 accepted of 19 tool calls |
| repeat_action_rate | 1 repeated of 19 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 11, 'validate': 2, 'apply_config': 6} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.assured_bandwidth.m1.json`](../../../records/qos.assured_bandwidth.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.assured_bandwidth.m1.judge.log`](../../../logs/judge/qos.assured_bandwidth.m1.judge.log)
