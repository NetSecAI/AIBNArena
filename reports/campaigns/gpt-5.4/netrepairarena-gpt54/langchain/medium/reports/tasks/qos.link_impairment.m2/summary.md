# qos.link_impairment.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 600497036, run `langchain_agent-gpt-5-4-qos-link_impairment-m2-fault-seed600497036-987efdd71aec`, recorded 2026-09-27T17:15:54 UTC.

## Outcome

| question | answer |
|---|---|
| repair oracle passed | no |
| full success (oracle, converged, subject completed and verified) | no |
| lab healthy before the fault | yes |
| fault degraded the lab as expected | yes |
| unaffected paths preserved | yes (score 1.0) |
| non-regression | yes |
| network converged to the reference | no |
| repair score | 0.0 |
| subject's own status | failed (verified no) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | own_conclusion: failed (turn 25) |
| model calls | 25 (tool calls 24) |
| wall clock | 177 s of a 400 s budget |
| time in the model | 41 s |
| tokens in / out | 397911 / 1500 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 18 (15) |
| device mutations (successful) | 4 (2) |
| validations | 6 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Initial validation showed intermittent loss only on finance1<->app1 (66.7% packet loss) while other paths passed. I confirmed leaf2 is the shared gateway and that finance and server interfaces/routing were present, then tested a narrow MTU change on leaf2 ethernet-1/30 and ethernet-1/50; post-change validation worsened to 100% loss with net-unreachable symptoms for affected paths, so I rolled that change back successfully. Public success criteria did not pass and no safe repair was validated within the attempt.

Subject error: Initial validation showed intermittent loss only on finance1<->app1 (66.7% packet loss) while other paths passed. I confirmed leaf2 is the shared gateway and that finance and server interfaces/routing were present, then tested a narrow MTU change on leaf2 ethernet-1/30 and ethernet-1/50; post-change validation worsened to 100% loss with net-unreachable symptoms for affected paths, so I rolled that

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 19 accepted of 24 tool calls |
| repeat_action_rate | 1 repeated of 24 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 18, 'validate': 2, 'apply_config': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.link_impairment.m2.json`](../../../records/qos.link_impairment.m2.json)
* trace: not recorded
* judge log: [`logs/judge/qos.link_impairment.m2.judge.log`](../../../logs/judge/qos.link_impairment.m2.judge.log)
