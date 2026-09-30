# qos.assured_bandwidth.m1

**Verdict: not repaired.**

Intent wording `high`, seed 38829221, run `langchain_agent-openai-qwen-qwen3-8-27b-qos-assured_bandwidth-m1-fault-seed38829221-55b0203720cd`, recorded 2026-09-29T13:41:34 UTC.

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
| termination | own_conclusion: failed (turn 1) |
| model calls | 1 (tool calls 0) |
| wall clock | 2 s of a 400 s budget |
| time in the model | 2 s |
| tokens in / out | 3382 / 44 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 0 (0) |
| device mutations (successful) | 0 (0) |
| validations | 4 |
| failed operations | 0 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Network repair not completed: the lab's public success criteria (user1→external1 throughput ≥8 Mbps under guest1 contention) were not validated as passing within this turn.

Subject error: Network repair not completed: the lab's public success criteria (user1→external1 throughput ≥8 Mbps under guest1 contention) were not validated as passing within this turn.

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 0 accepted of 0 tool calls |
| repeat_action_rate | 0 repeated of 0 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | early submission; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.assured_bandwidth.m1.json`](../../../records/qos.assured_bandwidth.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.assured_bandwidth.m1.judge.log`](../../../logs/judge/qos.assured_bandwidth.m1.judge.log)
