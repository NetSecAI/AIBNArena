# dhcp_dns.dhcp_provisioning.m2

**Verdict: not repaired.**

Intent wording `medium`, seed 450331920, run `langchain_agent-openai-qwen-qwen3-8-27b-dhcp_dns-dhcp_provisioning-m2-fault-seed450331920-ccb97f72692d`, recorded 2026-09-29T09:03:55 UTC.

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
| termination | budget: execution budget exceeded (turn 39) |
| model calls | 39 (tool calls 39) |
| wall clock | 400 s of a 400 s budget |
| time in the model | 160 s |
| tokens in / out | 1120204 / 2442 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 35 (31) |
| device mutations (successful) | 0 (0) |
| validations | 8 |
| failed operations | 8 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 35 accepted of 39 tool calls |
| repeat_action_rate | 2 repeated of 39 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 35, 'validate': 4} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/dhcp_dns.dhcp_provisioning.m2.json`](../../../records/dhcp_dns.dhcp_provisioning.m2.json)
* trace: not recorded
* judge log: [`logs/judge/dhcp_dns.dhcp_provisioning.m2.judge.log`](../../../logs/judge/dhcp_dns.dhcp_provisioning.m2.judge.log)
