# Reports

One folder per kind of test, so everything a run produces is under `reports/`:

| Folder | What it holds | Tracked |
| --- | --- | --- |
| `manual/` | runs launched by hand: one report per run, the judge's results under `results/` | no |
| `campaigns/` | campaign records laid out as `<model>/<experiment>/<subject>/<intent>/`, built by `scripts/export_results.py` (which the web interface runs when a campaign ends); the raw campaign runs they are built from sit under `runs/`, each with its campaign page in `reports/campaign.report.html` | yes, except `runs/` |
| `seed-registry.json` | what each seed stood for: the experiment, scenario, method, topology and resolved fault instance, written by the launcher on every run | yes |

A report is one benchmark result turned into something a person and a program can both
read. It is derived from the result, never a replacement for it: the result is the record
the judge wrote, the report is the reading of it.

`benchmarks/run.py` writes both at the end of every episode: the result in the
experiment's `result_dir` — `reports/manual/results/` unless a TOML or `--result-dir`
says otherwise — and the report in its `report_dir`, `reports/manual/` by default. The
campaign launcher sends both into `reports/campaigns/runs/<id>/`, results at the top
and reports in `reports/`. The two are kept apart because campaign scripts and summaries collect results as `*.json` in their
directory, and a `RUN.report.json` beside them would be taken for one. A report that
cannot be derived is reported on stderr and does not fail the run: the result is already
on disk. To derive one again, or from an older result:

```bash
./scripts/generate_report.py reports/manual/results/RUN.json
```

The input must validate against `benchmarks/core/result.schema.json`. One run produces:

| Output | What it is |
| --- | --- |
| `reports/manual/RUN.report.json` | the report as data, for further processing |
| `reports/manual/RUN.report.html` | the same document as a page |

Options: `-o BASE` names the base every output is built from (an extension on it is
dropped); `-f json|html|both`; `--no-trace` skips reading the episode's trace, leaving
the timeline with timings but no model text.

## A campaign's page

A campaign run from the web interface ends with one more page, beside its episodes'
reports: `reports/campaigns/runs/<campaign>/reports/campaign.report.html`, and the same
document as data in `campaign.report.json`. It is derived from the campaign's
`campaign.json` and the results it names, like an episode's report from its result:

* **the subjects compared**, each subject and model one column: the per-cell summary of
  `scripts/summarize_campaign.py` (repairs with their 95% Wilson interval, turns, output
  tokens, seconds, mutations, termination causes, flags), repairs by scenario family,
  the same plate (scenario, seed and fault) for every subject with who repaired what the
  other did not, how the episodes ended, the evaluation parameters of
  `scripts/evaluation_parameters.py`, and, for the subjects that retrieve from documents,
  what they retrieved;
* **every cell** of the matrix in its order, those that produced no result with the reason;
* **every episode's interactions**: its timeline as `generate_report.py` renders it, and a
  link to its own report.

The web interface writes it when the last cell ends. To build it again, for instance for
a campaign whose server was stopped before the end:

```bash
./scripts/generate_campaign_report.py reports/campaigns/runs/<campaign>
```

Options: `-o BASE`, `-f json|html|both` and `--no-trace`, as for an episode's report.

## Run conditions in a result

A result says what was set for the run as well as what happened. `provenance` names the
scenario wording the subject was given (`intent_variant`, null when the scenario states
one task), and `model_parameters` names the subject's own conditions: `prompt_variant`,
`ani_call_limit`, and how reasoning was asked for (`enable_thinking`, `thinking_effort`,
the resolved `thinking_mechanism` and the `thinking_request` actually sent).
`execution.interaction_limits` reports the ANI budget as limit, used, refused and
reached, counted by the ANI itself. An operation a cap refused carries
`dispatched: false` and counts as a failed operation only, never as a read, a write or a
validation that happened.

## The fields

Everything below is the JSON report. The HTML shows the same values, plus two rows in
its run table that this document's numbers are read from rather than stored in:

* `ani calls` — how many calls the subject made, split into reads, configuration
  changes and validations (`operation_counts`).
* `device changes` — how many devices it actually changed, on which, and how many the
  ANI accepted (`sut_result.device_changes`).

They are two counts of two different things and neither follows from the other: one
`update_object` naming two devices is one call and two changes. A report showing only
the second reads as though it hid the first.

### `report_version`, `source`

| Field | Meaning |
| --- | --- |
| `report_version` | bumped when a consumer must change to keep reading these files |
| `source.path` | the result file this report was derived from |
| `source.sha256` | its digest, so a report can be matched to the exact result after either moves |
| `source.schema_version` | the result schema version that file declared |

### `run` — what this run was

| Field | Meaning |
| --- | --- |
| `run_id`, `experiment_id` | identifiers the judge minted |
| `created_at` | when the run started (the only absolute timestamp in the report) |
| `status` | the judge's verdict on the run: `completed` or `failed` |
| `scenario.{id,version,domain,sut_task_id}` | the scenario, and the opaque task id the SUT was given |
| `sut_identity`, `sut_version` | which subject answered, from its Agent Card |
| `configured_model` | the model the SUT was configured with |
| `sut_reported_model` | the model the SUT says it used |
| `provider_reported_model` | the model the provider's own response reported — what answered, where the other two are what was asked for |
| `model_agreement` | whether the three agree: `agree` (null when fewer than two were recorded, so there was nothing to check), `known` by role, and the roles `missing`. The run table states it beside the name, because a report showing the declared model alone cannot be read for a gateway that aliased it |
| `sut_status` | the episode's own outcome: `completed`, `failed` or `timeout` |
| `sut_verified` | whether the SUT closed with a passing public validation |
| `final_summary` | the model's own one-line conclusion (see `timeline.final_response`) |
| `success` | the overall metric: environment, repair and preservation all passed |
| `budget_seconds` | the time the agent was granted |
| `elapsed_seconds` | the time the episode used |
| `budget_exhausted` | true when the loop cut the agent off at the deadline |
| `trace_ref` | the episode's trace file, where the model's own words live |

### `error`

`null`, or `{phase, type, message}` when the lifecycle failed — the phase names where.

### `evaluations[]` — the oracle verdicts

One entry per phase (`healthy`, `expected_degradation`, `repair`, `preservation`) with
`oracle_id`, `version`, `passed`, and `probe_count`. The probes themselves stay in the
source result; what a report needs is the verdict and how much it measured.

### `metrics`

The judge's own metrics, verbatim. `success` is the headline; `repair_score`,
`preservation_score`, `path_repair_pass_rate` and `non_regression_passed` say what it was
made of; `converged` comes from the post-repair observation; `sut_completed` and
`sut_verified` are the SUT's claims, kept apart from the measurements.

Note: `mutation_count`, `read_count`, `validation_count` and the operation counts here are
the judge's accounting, which differs from the report's — see the next section.

### `operation_counts` — derived from the operation ledger

| Field | Meaning |
| --- | --- |
| `llm_calls` | model calls |
| `containerlab_reads` | device reads: `get_topology`, `get_state`, `get_running_config`, `get_object` |
| `containerlab_mutations` | calls that changed a device: `update_config`, `update_object`, `rollback_config`. A call, not a device: one call naming two devices is one mutation and two `sut_result.device_changes` |
| `containerlab_mutations_ok` | those the ANI confirmed |
| `sut_validations` | the agent's own `execute_validation` calls |
| `failed_operations` | tool calls that came back `ok: false` |
| `unsafe_operations` | operations the safety gate refused |
| `input_tokens`, `output_tokens`, `total_tokens` | token usage, `null` when the provider reported none |
| `recorded` | the judge's own `operation_counts`, whole and unmodified |

One of these deliberately disagrees with `recorded`:

* `sut_validations` counts the agent's calls only. The judge's `validations` also
  includes its own oracle runs — healthy, each degradation draw that measured, repair,
  preservation — so it is normally the larger number.

### `phase_durations`

The seventeen lifecycle phases, in seconds. Their sum is the run's wall clock; no field
carries that total. `invoke_sut` is the episode measured on the judge's side of the A2A
boundary — compare it with `run.elapsed_seconds`, measured inside the SUT, and the
difference is transport.

### `timeline` — the episode in order

| Field | Meaning |
| --- | --- |
| `trace.{path,available,reason}` | the trace file, and why it could not be read when it could not |
| `turns` | how many turns the model took |
| `seconds.thinking` | time inside model calls |
| `seconds.tools` | time inside tool calls |
| `seconds.episode` | the episode's elapsed time |
| `seconds.unaccounted` | the rest: transport and the loop's own bookkeeping, reported rather than distributed, because nothing measured it |
| `final_response` | the model's final status object: `{status, summary}` |
| `budget.granted_seconds` | the time the agent was allowed |
| `budget.used_seconds` | the time it used |
| `budget.remaining_seconds`, `budget.used_ratio` | what was left, and the fraction spent |
| `budget.exhausted` | the loop's verdict: the episode was cut off at the deadline (`sut_status: timeout`) |
| `budget.exceeded` | arithmetic: used beyond granted. It can be true without `exhausted` — a turn or a call already in flight at the deadline is waited for, not interrupted |
| `budget.overrun_seconds` | by how much |
| `budget.invoke_sut_seconds` | the same episode from the judge's side; its client gives up at `budget + 120 s`, so a large overrun appears there as a failed phase rather than a long one |
| `steps[]` | thinking and calls, interleaved in the order they happened |

Each step carries `step` (its rank in the timeline) and `type`.

A `thinking` step is **one turn of the model**:

| Field | Meaning |
| --- | --- |
| `turn` | its rank among the model's turns |
| `seconds` | how long the model took, measured around the call and closed before the tools run |
| `kind` | how the turn ended: `tool_calls`, `final`, `rejected_final` (completion claimed without a passing validation), `malformed_tool_call`, `no_action`, `error` |
| `tool_calls` | what the model decided to call at the end of that turn |
| `content_chars` | how much prose the turn produced |
| `text`, `reasoning` | the model's words, from the trace: the answer, and the reasoning when the provider returned it apart. Both `null` when no trace was kept |
| `error` | why the turn was rejected, when it was |

A `call` step is one dispatched tool call:

| Field | Meaning |
| --- | --- |
| `position` | its rank among all operations (`sut_result.ani_operations`) |
| `tool`, `category` | the tool, and what the call turned out to do: `read`, `mutation`, `validation`, `tool` |
| `ok`, `seconds`, `error` | outcome, duration, failure |
| `arguments` | the parameters the model chose, verbatim |

A turn claims its calls by position, so an operation no turn claims — one a subject's own
tool issued, evidence a SUT staged before the first turn — is still emitted where it
happened.

Reasoning reaches the report from the trace file and never from the benchmark result:
`sut/common/trace.py` keeps private reasoning out of what the scorer reads, and the report
is the analysis artifact, not the scored one. The model's text therefore requires a run
made with tracing at level `full` and a trace directory; without it the timeline still has
every turn's timing, and `content_chars` says how much text existed.

### `provenance`

Copied whole from the result: the three model names, the A2A SDK version, the SUT identity
and version, `model_parameters` (including how thinking was requested), the git commit and
whether the tree was dirty, the scenario version and seed, oracle versions, and the
topology and reference-state digests.

### `sut_result`, `convergence_result`, `cleanup_result`

Copied whole, so a report is readable on its own. `sut_result` is where the derived
sections come from: `ani_operations` (the operation ledger), `device_changes` (the device changes
and their verification), `execution` (budget, elapsed, token usage, `model_turns`,
`trace_ref`) and `final_response`. `convergence_result`
holds the post-repair observation and the polling parameters — `interval_seconds` and
`timeout_seconds` are configuration, not measurements; no convergence time is recorded.
`cleanup_result` is what the testbed teardown did.
