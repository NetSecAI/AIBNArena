# dhcp_dns.dhcp_provisioning.m2

**Verdict: repaired by the oracle's reading, but the subject did not conclude.**

Intent wording `medium`, seed 450331920, run `langchain_rag_agent-openai-qwen-qwen3-8-27b-dhcp_dns-dhcp_provisioning-m2-fault-seed450331920-aa622d8847cd`, recorded 2026-09-29T14:19:10 UTC.

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
| subject's own status | failed (verified yes) |
| judge lifecycle | completed |

## Execution

| measure | value |
|---|---|
| termination | budget: execution budget exceeded (turn 49) |
| model calls | 49 (tool calls 52) |
| wall clock | 416 s of a 400 s budget |
| time in the model | 250 s |
| tokens in / out | 2276249 / 4005 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 37 (36) |
| device mutations (successful) | 11 (8) |
| validations | 8 |
| failed operations | 7 |
| unsafe operations | 0 |

## The subject's conclusion

No conclusion was returned.

Subject error: execution budget exceeded

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | passed; oracle verdict reached |
| tool_call_success_rate | 47 accepted of 52 tool calls |
| repeat_action_rate | 13 repeated of 52 tool calls |
| time_limit_rate | hit the time limit |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: no, declared completed: no |
| error_submission_rate | no |
| llm_found_problem_rate | fault located yes; a change reached the faulty device yes |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 37, 'validate': 4, 'apply_config': 11} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/dhcp_dns.dhcp_provisioning.m2.json`](../../../records/dhcp_dns.dhcp_provisioning.m2.json)
* trace: not recorded
* judge log: [`logs/judge/dhcp_dns.dhcp_provisioning.m2.judge.log`](../../../logs/judge/dhcp_dns.dhcp_provisioning.m2.judge.log)
