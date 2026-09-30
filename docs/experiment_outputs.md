# Reading Experiment Outputs

An experiment produces two views of the same episode:

1. the **SUT report**, which records what the agent attempted and what it concluded from public ANI evidence;
2. the **benchmark result**, which embeds that report and adds the Judge's independent lifecycle, oracle verdicts, metrics, provenance, and cleanup evidence.

The benchmark result is authoritative. The SUT report is diagnostic.

## Where Outputs Are Written

A campaign created by [`experiments/run_langchain_baseline.sh`](../experiments/run_langchain_baseline.sh) uses this layout:

```text
reports/campaigns/runs/<campaign-id>/
├── langchain-<model>-<scenario>-seed-<seed>-<run-id>.json
└── server-logs/
    ├── langchain-<model-a>.log
    └── langchain-<model-b>.log

reports/manual/results/artifacts/
└── ani-<sha256>.json
```

- Each top-level campaign JSON file is one benchmark episode.
- Server logs contain the human-readable SUT summary and HTTP/runtime logs.
- ANI artifacts contain complete tool responses that were paged or truncated before being shown to the model.
- Generated results and logs are ignored by Git. A research release should publish selected immutable datasets separately with checksums.

## Three Different Status Layers

Do not interpret a single `status` field in isolation.

| Layer | Fields | Meaning |
|---|---|---|
| Lifecycle | top-level `status`, `error` | Whether the benchmark lifecycle completed without an infrastructure/setup exception. |
| SUT | `sut_result.status`, `sut_result.verified`, `sut_result.error` | Whether the agent completed and passed its own public ANI validation. |
| Final verdict | `metrics.success` | Whether the independently evaluated environment, convergence, SUT completion, and SUT verification all passed. |

A top-level `status: completed` does **not** mean the repair succeeded. It means the lifecycle reached scoring, cleanup, and result writing without a lifecycle exception.

The shared final verdict is equivalent to:

```text
metrics.success =
    metrics.environment_success
    and metrics.converged
    and metrics.sut_completed
    and metrics.sut_verified
```

## Recommended Reading Order

### 1. Confirm lifecycle integrity

Read:

```text
status
error
cleanup_result
```

- `status: failed` means a lifecycle phase raised an exception.
- `error.phase` identifies the failing phase.
- `cleanup_result.ok` confirms whether restoration or destruction succeeded.
- A failed lifecycle can still have a valid result file and partial evidence.

### 2. Confirm provenance

Read `provenance` before comparing models or aggregating runs:

| Field | Check |
|---|---|
| `configured_model` | The model the campaign meant to run, as the judge was told (`--configured-model`); `null` when it was not told. Results written before this field was independent carry a copy of `sut_reported_model` here. |
| `sut_reported_model` | Model identity reported in the SUT report. |
| `provider_reported_model` | Model identity returned by the provider, when available. The only one of the three that is evidence of what served the episode, rather than a copy of what was asked for; `summarize_campaign` raises `model_mismatch` when it disagrees with either of the others. |
| `sut_identity`, `sut_version` | Exact implementation that handled the task. |
| `model_parameters` | Token, temperature, context, budget, and recursion settings. |
| `git.commit`, `git.dirty` | Source revision and whether a tracked file was modified; `modified_files` and `untracked_files` count both kinds. Results written before the counts existed set `dirty` on untracked files too. |
| `scenario_seed` | Deterministic scenario binding. |
| `oracle_versions` | Oracle versions used for each phase. |
| topology/reference hashes | Exact input fingerprints. |

Requested, configured, SUT-reported, and provider-reported models should agree before a run enters a model comparison.

### 3. Check episode validity

Read `evaluations.healthy` and `evaluations.expected_degradation`:

- **healthy** must pass before injection, proving the reference state was usable;
- **expected_degradation** must pass after injection, proving the selected fault produced the intended observable degradation.

`evaluations.expected_degradation.injection` records how that verdict was reached: `attempts_allowed` is the configured `injection_attempts`, and `attempts` lists every draw with `restored`, `reinjected`, `degraded`, and `voided` (true when the draw's probe reported `contention_observed: false`, so the instrument, not the fault, decided it); aborted attempts also carry `error`. A `degraded` of `null` marks an attempt that aborted before measuring (restore or re-injection failed), which is a different finding from a fault that was measured and not seen. When the evaluator raised before an `expected_degradation` evaluation existed, the attempts recorded up to that point are kept under `metrics.injection` instead, with the same shape; a result never carries both.

If either fails, the episode is not valid evidence of agent capability.

### 4. Read the independent repair verdict

Read:

```text
evaluations.repair
metrics.environment_success
metrics.repair_score
```

The repair oracle is evaluated by the Judge after the SUT returns. `environment_success` summarizes the required oracle phases, not the model's narrative.

Domain-specific metrics, such as `path_repair_pass_rate`, are added under `metrics`.

### 5. Read preservation separately

Read:

```text
evaluations.preservation
metrics.non_regression_passed
metrics.preservation_score
metrics.preservation_vacuous
```

Preservation asks whether unrelated healthy network behavior remained intact. It is different from repair correctness:

- repair can fail while unrelated paths remain preserved;
- repair can pass while an unrelated service regresses;
- final-state preservation does not reveal every unsafe intermediate action.

A preservation pass can be vacuous. When every required path touches an affected node, the unaffected scope selects nothing, the platform records zero checks with `vacuous: true` on the probe, and the oracle passes. The lifecycle surfaces this as `metrics.preservation_vacuous` (and `metrics.non_regression_vacuous` in the domain projection): `true` when every preservation probe was vacuous, `false` when at least one probe measured something, `null` when the scenario ran no preservation oracle. The episode still passes (`preservation_score` and `success` are unchanged), but a vacuous pass is not evidence that anything was preserved, so any aggregate over preservation results must filter on the marker and report the vacuous count separately, as [`analysis/openweight_report.py`](../analysis/openweight_report.py) does.

### 5b. Read a no-fault episode on its own terms

A run started with `--no-fault` (or a suite whose `fault_applicable = false`) keeps the same compiled instance, bindings and task, and never injects the fault: the subject is handed a healthy lab with nothing to repair. `metrics.fault_applicable` is `false`, `evaluations` carries no `expected_degradation`, and `phase_durations.inject_fault` and `evaluate_degradation` read `0.0`. `metrics.false_positive` is `true` when the subject asked the ANI for at least one mutation on that lab, and `success` is then `false` whatever the oracles said. A refused or failed attempt counts the same as an applied one: what the plate measures is the subject deciding a healthy network needed changing, and a guard that stopped the write is the platform's doing rather than the subject's. Read `operation_counts` alongside it to tell the two apart -- `ani_mutations` is what was asked for, `successful_mutations` what landed. On a fault episode `false_positive` is `null`: the question was not asked. Aggregate the two kinds of episode apart.

### 6. Check convergence

`convergence_result` records the bounded wait between the SUT response and independent verification.

- `converged: true` means the repaired data plane reached the platform's healthy convergence condition.
- `converged: false` is scoring evidence, not automatically a lifecycle exception.
- `timeout_seconds` and `interval_seconds` show the polling policy.
- `observation` contains the final convergence evidence.

This prevents routing or interface convergence delay from being mistaken for an immediate agent failure.

### 6b. Read the device configurations before and after the subject

`device_configurations` (records from 2026-09-16 on; `null` when the platform cannot read device
configuration) points at a directory beside the record, `<result_dir>/devices/<scenario>-<stamp>-<id>/`,
holding the running configuration of every device at three moments, one file per device and moment:

- `healthy.txt`: after the reference state, before the fault;
- `before_sut.txt`: the faulty network the subject was handed;
- `after_sut.txt`: what the subject left, read before the convergence wait.

Each is read the way the ANI's `get_running_config` reads it for the subject (SR Linux `info from
running /`, VyOS the active configuration tree, Linux `ip address show` and `ip route show`). The
diffs `healthy_to_before_sut.diff` (what the fault changed) and `before_sut_to_after_sut.diff` (what
the subject changed) exist only for the devices whose configuration differs; Linux address lifetimes
are ignored. The record's `device_configurations.changes` lists those devices with their line counts,
`changed_by_sut` names the devices the subject touched, and `snapshots.<moment>.devices.<name>.ok`
says whether each read succeeded. `manifest.json` in the directory repeats the record's block.

### 7. Inspect the embedded SUT report

The complete A2A response is stored under `sut_result`.

| Field | Meaning |
|---|---|
| `mode` | Must be `self_execute` for the active contract. |
| `status` | SUT-side completion state. |
| `verified` | Whether the latest public success-criteria validation passed. |
| `device_changes` | Every device change the subject made, with its transaction evidence; `actions` in records written before ANI v0.2. |
| `ani_operations` | Ordered summaries of reads, mutations, validations, failures, durations, and artifact references. |
| `execution` | Budget, elapsed time, call counts, model parameters, token usage, and provider model. |
| `execution.termination` | Why the loop stopped: `{cause, detail, turn}`. Causes: `own_conclusion` (the model returned a final status), `budget` (the clock ran out), `completion_rejected_until_budget` (the last five turns were completion claims the gate refused), `completion_rejected` (the configured cap on consecutive refused claims), `context_window` (the provider rejected the transcript), `context_budget` (the configured transcript budget was exceeded before the call), `model_error` (any other provider error), `dry_run`. Absent in results written before this field; read `status` and `error` there. |
| `execution.reservation` | `{reductions, min_max_tokens}`: how many times the backend halved its output reservation on a provider context rejection and the smallest reservation a call finished on. A run that lived at a few tokens is visible here, not in `status`. |
| `execution.context` | The transcript account: the bounds in force (`budget_chars`, `tool_result_chars`, `max_consecutive_rejections`, all `null` unless the run file set them), `peak_prompt_chars` measured before each call, `truncated_results`, `elided_results`, and `events` for each cut or elision. Under the defaults every result travels whole and only the peak is measured. |
| `final_observation` | Latest public validation evidence visible to the SUT. |
| `summary` | Concise model-produced conclusion, when available. |
| `error` | Timeout, loop guard, validation rejection, provider error, or another SUT-side failure. |

`sut_result.verified` is useful diagnostic evidence but never replaces the private repair and preservation oracles.

### 8. Interpret operation counts and cost

Top-level `operation_counts` is designed for aggregation:

- `ani_reads` and `ani_mutations` come from the SUT's ANI report and count attempts; `successful_reads` and `successful_mutations` count the ones that returned `ok: true`. A read the router refused is still an attempt (`ani_reads`) and a `failed_operations` entry, but not a `successful_reads` one;
- `successful_mutations` counts accepted mutations;
- `execution.interaction_limits` reports the ANI budget as limit, used, refused and reached, counted by the ANI, so it includes the operations a subject's own tool performed while answering one model call. An operation a cap refused carries `dispatched: false`, counts as a failed operation, and is claimed by no category counter. A subject that exhausts its ANI budget cannot run `execute_validation`, so it cannot satisfy the completion gate and the episode ends without a verified claim, which is what running out of interactions means;
- `successful_reads`, added on 2026-09-04, is optional in `result.schema.json`, so every result written before it existed keeps validating and rendering. The reverse does not hold: a result written by this judge carries it, and a checkout whose schema predates it refuses it, so `benchmarks/` and `scripts/generate_report.py` have to move together.
- `failed_operations` counts failed SUT ANI operations;
- `unsafe_operations` counts operations classified as unsafe by the ANI reporting path;
- `llm_calls` and token fields measure model interaction cost;
- `validations` includes SUT validations **and** the Judge's oracle evaluations; every injection attempt that actually measured counts once, while an attempt that aborted before measuring (`degraded: null`) does not.

For the exact SUT-only operation sequence, use `sut_result.ani_operations`.

### 9. Use phase durations for performance analysis

`phase_durations` separates environment cost from agent cost:

- `invoke_sut` is the end-to-end A2A/SUT duration;
- deployment, reference application, and oracle phases measure benchmark overhead;
- `snapshot_healthy`, `snapshot_before_sut` and `snapshot_after_sut` are the per-device configuration reads (6b);
- `evaluate_repair_preservation` includes convergence waiting and final probes;
- `restore_destroy` measures cleanup;
- `write_result` measures persistence.

Use `sut_result.execution.elapsed_seconds` for the SUT's own measured runtime and `phase_durations.invoke_sut` for the Judge-observed request duration.

## Common Outcome Patterns

| Pattern | Interpretation |
|---|---|
| top-level `failed`, no SUT operations | Setup, compilation, A2A, or another lifecycle failure; inspect `error.phase`. |
| SUT `failed`, `verified: false`, environment failed | The agent did not produce a publicly validated repair and the private oracle agrees. |
| SUT `failed`, environment repaired | The lab ended healthy, but the agent did not satisfy the SUT completion/validation contract; final success remains false. |
| SUT `completed`, `verified: true`, environment failed | Public validation and the private oracle disagree; inspect their scopes and evidence. |
| repair passed, preservation failed | The target was repaired but unrelated healthy behavior regressed. |
| environment passed, `converged: false` | Final probes passed but the platform convergence condition did not; final success remains false. |
| `metrics.success: true` | Lifecycle evidence, convergence, independent repair, SUT completion, and SUT verification all align. |

## Useful Commands

Set the result once:

```bash
RESULT=reports/campaigns/runs/<campaign-id>/<result-file>.json
```

Read the compact verdict:

```bash
jq '{
  lifecycle_status: .status,
  lifecycle_error: .error,
  benchmark_success: .metrics.success,
  environment_success: .metrics.environment_success,
  converged: .metrics.converged,
  preservation: .metrics.non_regression_passed,
  sut_status: .sut_result.status,
  sut_verified: .sut_result.verified,
  sut_error: .sut_result.error
}' "$RESULT"
```

Audit provenance:

```bash
jq '.provenance | {
  configured_model,
  sut_reported_model,
  provider_reported_model,
  sut_identity,
  sut_version,
  model_parameters,
  scenario_seed,
  git
}' "$RESULT"
```

Inspect oracle phases:

```bash
jq '.evaluations | to_entries[] | {
  phase: .key,
  oracle: .value.oracle_id,
  passed: .value.passed,
  probes: .value.probes
}' "$RESULT"
```

Inspect agent actions and ANI calls:

```bash
jq '.sut_result.actions' "$RESULT"
jq '.sut_result.ani_operations' "$RESULT"
```

Compare SUT runtime with Judge-observed duration:

```bash
jq '{
  sut_elapsed_seconds: .sut_result.execution.elapsed_seconds,
  judge_invoke_seconds: .phase_durations.invoke_sut,
  llm_calls: .operation_counts.llm_calls,
  ani_reads: .operation_counts.ani_reads,
  ani_mutations: .operation_counts.ani_mutations,
  total_tokens: .operation_counts.total_tokens
}' "$RESULT"
```

## Reading a Campaign Rather Than an Episode

Everything above reads one result. A campaign asks a different question -- of the
episodes that failed, what failed about them -- and
[`scripts/evaluation_parameters.py`](../scripts/evaluation_parameters.py) answers it
with the rates the testbed parameter sheet names. It derives every one from
the recorded episode, so a campaign already on disk is measurable without re-running
it, and a definition that turns out wrong is corrected by re-running the script rather
than by another field frozen into two hundred result files.

```bash
python scripts/evaluation_parameters.py runs/20260906 --by subject
python scripts/evaluation_parameters.py runs/20260906 --json --out report.json
```

Default grouping is `subject,condition`. The full cell key leaves about three episodes
per cell, where a rate can only be 0, a third, two thirds or 1. `--by subject,intent`
splits a campaign by the wording of the task instead (`provenance.intent_variant`:
low, medium, high), which is how a run that varies the intent's precision is read;
a record written before the judge named the wording reads as `unrecorded`.

### The four statuses

A rate is an object, never a bare float, because a denominator that does not exist is
not a zero and must not read as one.

| status | `value` | means |
|---|---|---|
| `measured` | the rate | a real number over a real denominator; a measured `0.0` keeps its denominator |
| `no_denominator` | `null` | the campaign never asked. `interaction_limit_rate` where no episode ran under a cap, `false_positive_rate` where every episode injected a fault |
| `structurally_zero` | `0.0` | the numerator cannot exist, because the surface has no such operation. `wait` is the only member |
| `unavailable` | `null` | the record carries no signal for it |

`value` is `null` unless the status is `measured` or `structurally_zero`, so a consumer
writing `value or 0` raises rather than quietly inventing a number.

### What each parameter counts

| parameter | numerator over denominator |
|---|---|
| `pass_rate` | episodes whose repair oracle passed, over episodes it judged |
| `tool_call_success_rate` | model actions the surface accepted, over model actions |
| `interaction_limit_rate` | episodes that reached a cap, over episodes that ran under one |
| `time_limit_rate` | episodes the wall clock ended, over scored episodes |
| `repeat_action_rate` | calls repeating an earlier operation with identical arguments; a call, not a device change |
| `early_submission_rate` | submissions made without validating, over submissions whose episode recorded an operation log |
| `error_submission_rate` | submissions the repair oracle did not pass, over submissions it judged |
| `ani_call_type_ratio` | the six-bucket histogram below, over the subject's calls, not over device changes |
| `score` | faults repaired over faults injected; one fault per episode today, so it equals `pass_rate` by arithmetic |
| `false_negative_rate` | fault episodes where the subject reported finding no problem |
| `false_positive_rate` | no-fault episodes where the subject changed a lab that needed no change |
| `llm_found_problem_rate` | episodes whose stated diagnosis the judge found to name the injected fault, over fault episodes the judge assessed; the device-based proxy stays beside it under `proxy` |

### Denominators that a test parameter creates

Four of them are gated by an input the campaign has to exercise, and until it
does they report honestly rather than as zero. `interaction_limit_rate` needs a run
under a cap. `score` becomes its own axis only once a scenario injects more than one
fault. And the two detection errors need the negative class: `benchmarks/run.py
--no-fault` runs the same plate on a healthy lab, where any successful change is the
false positive and `metrics.false_positive` records it. A campaign that injects a fault
into every episode contains nothing a false alarm could be about, so
`false_positive_rate` has no denominator there -- reporting `0.000` would say the
subject never raised a false alarm when the truth is that it was never given the chance.

`false_negative_rate` is `unavailable` for a different reason: no subject states a
detection verdict as such. The diagnosis step (below) records what the subject said
the fault was, and a statement that no fault was found is judged as not naming the
injected one, but reading that statement as a detection verdict is a separate
definition this parameter does not yet have.

### Parameter 13: the diagnosis step and its judge

The sheet asks for the LLM's own explanation of the fault compared against the
scenario's methods. Since 2026-09-24 the record carries both halves. The subject
states the fault it identified (`diagnosis` in its final JSON, or one tool-less turn
after the clock; `sut_result.diagnosis` says which), the judge puts the injected
fault into words from the compiled method and its bindings, and ParaPLUIE compares
the two: a language model's log-probability of "Yes" minus that of "No" after a
task-specific question, positive meaning the same fault. `metrics.diagnosis` carries
the reference, the hypothesis and its source, the score, `found`, and which judge and
prompt produced it; `found` is null, with a `reason`, wherever the question was not
asked (no judge configured, a judge that failed, no fault injected). The full method
and its configuration are in [`diagnosis_judge.md`](diagnosis_judge.md).

`llm_found_problem_rate` is then found over judged, an episode that stated nothing
counting as not found, with `hypothesis_sources` saying how many statements came from
the conclusion, the termination turn, or a pre-step record's summary scored offline
(`scripts/score_diagnoses.py`). The reading the parameter stood in with before, which
device the agent went to, stays beside it under `proxy`, and is still the whole
answer for a campaign with no judged episode: the two differ in both directions,
since an agent can name the cause and edit elsewhere, or blunder onto the right
device with no idea why.

The stricter reading -- a change to the faulty device that went through -- stays beside
it as `device_write_numerator`.

The faulty device is not in the result file. It is recovered by replaying the scenario
binding from `provenance.scenario_seed` against the topology
`provenance.topology_sha256` identifies, through the compiler's own
`compile_scenarios` entry point rather than a hand-rolled loop over the domain -- the
one seeded stream every binding is drawn from is moved by which files the walk visits,
and the production walk skips what the topology-applicability map excludes. An episode
that asked to change nothing located nothing and counts against; only a digest this
checkout cannot reproduce leaves the denominator, since binding a different device
silently would score the agent against the wrong answer and that blindness is ours
rather than its. A checkout that cannot import the compiler at all reports the whole
parameter as having no denominator, naming the import error -- folded into the
per-episode failure it read as a confident zero over the episodes that happened to
survive. The offline scorer reads the injected method from the same replay.

### The six action buckets

The sheet names four -- searching, apply config, check config, wait -- and the surface
has more: `search`, `check_config`, `apply_config`, `wait`, `validate`, `unknown`.
`search` and `wait` are structurally zero: no search and no wait exist on the ANI, and
the two buckets are kept because the sheet names them.

### Four ways the record misleads a first reading

Each is a named test in
[`scripts/tests/test_evaluation_parameters.py`](../scripts/tests/test_evaluation_parameters.py)
that fails when its guard is removed.

1. **`ok` is a verdict, not the health of a call.** `execute_validation` sets it from
   whether the criteria passed, so an honest failing validation carries `ok: false` with
   no error; `update_config` can carry it on an indeterminate dispatch. Acceptance is
   the absence of `error`. Reading `ok` reports the agent's measurements as its mistakes.
2. **Some rows are the SUT talking to itself.** A subject tool that performs ANI
   operations on its own account tags each row with `source`; counting those makes the
   repeat rate a measure of the subject's plumbing rather than of the agent.
3. **A judge-side failure leaves `sut_result` an empty object,** not absent. A testbed
   that never came up is not a subject that scored nothing, and counting those episodes
   puts a zero into every rate for a run the agent never got to make.
4. **`metrics.validation_count` is not how often the subject validated.** It is the
   judge's four oracle runs plus the subject's calls.

And one that is not about the record: `metrics.false_positive` already exists and means
a successful mutation on a scenario that had no fault. The name is spent.

### Why `score` equals `pass_rate`, and what would change it

Not a placeholder. A method binds exactly one fault and every scenario states one
method, so the denominator is one. Partial credit needs a per-fault verdict, and the
repair oracle answers once for the whole episode with no fault index on it or its
probes -- a scenario stating two faults would score an episode that fixed one of them
as zero. The probe count is not a stand-in either: probes are checks, not faults, and
`dhcp_dns.dhcp_provisioning.m3` and `.m4` break DNS while leaving ICMP intact, so their
path probe passes with nothing repaired at all. Counting probes credits the agent for a
check that was never broken.

The persisted structure is enforced by [`benchmarks/core/result.schema.json`](../benchmarks/core/result.schema.json). Result construction lives in [`benchmarks/core/lifecycle.py`](../benchmarks/core/lifecycle.py), SUT report parsing in [`benchmarks/core/sut_client.py`](../benchmarks/core/sut_client.py), and atomic persistence in [`benchmarks/core/reporting.py`](../benchmarks/core/reporting.py).
