# qos.assured_bandwidth.m1

**Verdict: not repaired.**

Intent wording `high`, seed 38829221, run `langchain_rag_agent-gpt-5-4-qos-assured_bandwidth-m1-fault-seed38829221-9468f7af4ca7`, recorded 2026-09-29T07:47:09 UTC.

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
| termination | own_conclusion: failed (turn 24) |
| model calls | 24 (tool calls 21) |
| wall clock | 307 s of a 400 s budget |
| time in the model | 49 s |
| tokens in / out | 524032 / 1232 |

## Operations

| operation | count |
|---|---|
| ANI reads (successful) | 15 (14) |
| device mutations (successful) | 3 (2) |
| validations | 7 |
| failed operations | 5 |
| unsafe operations | 0 |

## The subject's conclusion

Status `failed`.

> Observed that internal lab paths were healthy but external connectivity for user1 and guest1 was already failing badly before any QoS repair: initial public validation showed 66-100% loss and ~1.2 s RTT to external1, with throughput measurement 0.0 Mbps. Reads showed wan1 had only a root HTB qdisc visible on eth2, but the ANI would not allow safe tc class/filter inspection or Linux tc changes. I tested one narrow routing hypothesis on leaf1 by pinning 203.0.113.0/24 to spine1, but validation worsened user1/guest1 external reachability, so I changed leaf1 to the alternate spine next hop. Final targeted ICMP checks still showed severe loss both directions between user1/guest1 and external1 (33-66% loss, very high RTT), so public success criteria could not be met safely.

Subject error: Observed that internal lab paths were healthy but external connectivity for user1 and guest1 was already failing badly before any QoS repair: initial public validation showed 66-100% loss and ~1.2 s RTT to external1, with throughput measurement 0.0 Mbps. Reads showed wan1 had only a root HTB qdisc visible on eth2, but the ANI would not allow safe tc class/filter inspection or Linux tc changes. I t

## How this episode counts in the evaluation parameters

| parameter | this episode |
|---|---|
| pass_rate | failed; oracle verdict reached |
| tool_call_success_rate | 19 accepted of 21 tool calls |
| repeat_action_rate | 4 repeated of 21 tool calls |
| time_limit_rate | within the budget |
| interaction_limit_rate | no limit hit (uncapped) |
| early_submission_rate | no; concluded by itself: yes, declared completed: no |
| error_submission_rate | submission with the repair not passed |
| llm_found_problem_rate | fault located no; a change reached the faulty device no |
| false_positive_rate | not a no-fault episode |
| ani_call_type_ratio | {'check_config': 15, 'validate': 3, 'apply_config': 3} |

## Files

* in this folder: `report.html` (the per-task report in the shared format), `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`, `provenance.json`
* record: [`records/qos.assured_bandwidth.m1.json`](../../../records/qos.assured_bandwidth.m1.json)
* trace: not recorded
* judge log: [`logs/judge/qos.assured_bandwidth.m1.judge.log`](../../../logs/judge/qos.assured_bandwidth.m1.judge.log)
