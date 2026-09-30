# AIBNArena web interface

Local pages over the same machinery, each named in the header under the title:

- **Episode** — one episode by hand: pick a subject, a model, a topology, a
  scenario and its method, a seed and a time budget, then watch the subject
  start, the judge run and the verdict arrive.
- **Campaigns** — a matrix of them: several subjects, several models, several
  scenarios (some as false positives) and several seeds, run in the order that
  restarts a subject as rarely as it can.
- **Models** — the endpoints the models are served from, and their keys
  (see [Models and their keys](#models-and-their-keys)).

It automates nothing the command line cannot do. It runs the same two commands
`experiments/run_langchain_baseline.sh` runs, in the same order. One episode
writes to `reports/manual/` — results under `reports/manual/results/`, reports
in `reports/manual/`, the two process logs under
`reports/manual/webui-logs/<run>/`. A campaign writes a campaign directory
under `reports/campaigns/runs/`, where the shell script already writes
its own.

## Install and run

```bash
.venv/bin/python -m pip install -r requirements-webui.txt
.venv/bin/python -m webui.main          # http://127.0.0.1:8080
```

`IBN_WEBUI_HOST`, `IBN_WEBUI_PORT` and `IBN_WEBUI_PYTHON` override where it
listens and which interpreter it starts the subject and the judge with. It binds
to the loopback address because it starts processes and drives a lab: it is a
local tool, not a service to publish.

## What one launch does

1. **Validates** the configuration, the scenario and its oracles by calling
   `benchmarks/run.py`'s own `validate_bundle()`. Nothing is started, so a bad
   combination costs a second rather than a lab deployment. The **Validate**
   button stops here and shows what would run.
2. **Frees the subject's port**, naming in the log any process it had to stop.
3. **Starts the subject** (`python -m sut.<subject>.a2a_server ...`).
4. **Proves it**: polls `/.well-known/sut-runtime.json` until the process id,
   the subject identity and the configured model are the ones just started. A
   listener left over from an earlier run answers too, and the episode would
   then be attributed to a model that never ran.
5. **Runs the judge** (`benchmarks/run.py`), streaming its output to the page.
6. **Stops the subject**, whether the episode succeeded, failed or raised.
7. **Reads the result** it wrote and shows the verdict, with links to the result
   and to the episode's report. The interface serves `reports/` read-only at
   `/reports/`, and nothing outside it, so what the judge wrote opens in the
   browser.

The run page follows three streams. The judge and the subject each get a column
of their own, side by side, because they run at the same time and the question
being watched is usually what one did while the other was doing something. Above
them sits what the launcher itself did between the two: the port it freed, the
subject it proved, the result it collected. Each pane scrolls on its own and
sticks to the bottom only while it is already there, so reading back through the
subject's output does not fight the judge's next line.

One run at a time, counting both pages: a launch while an episode or an
campaign is in flight is refused, not queued, because both would deploy and
mutate the same containerlab lab. The interlock is one registry rather than one
per page, so a campaign cannot start on top of a running episode.

## Campaigns

The **Campaigns** page runs a matrix instead of one episode: subjects ×
models × selected scenarios × seeds. Each scenario is a row, and a row ticked
**false positive** is the no-fault episode of that same scenario, so a scenario
can appear twice — once repaired, once on a healthy lab.

Every cell is an ordinary `EpisodeRequest`. Nothing about running an episode is
reimplemented for matrices: the same argv is built, the same flag a subject does
not accept is refused, the same judge writes the same record.

Each scenario is added as a row, or a whole experiment at once: **Add all N
scenarios and methods** fills the table with every scenario and method that
experiment offers — twenty-one rows for connectivity — skipping the ones already
there rather than repeating them. Because one click can turn into a hundred
episodes, the chip beside the launch button carries the cell count and the
episode budgets added up, and goes amber past four hours.

A campaign writes where the campaign script already writes:

```
reports/campaigns/runs/<name>-<date>/            the records
reports/campaigns/runs/<name>-<date>/reports/    the reports, the campaign's page among them
reports/campaigns/runs/<name>-<date>/server-logs/    one per subject started
reports/campaigns/runs/<name>-<date>/judge-logs/     one per cell
reports/campaigns/runs/<name>-<date>/launcher.log    what this tool did between the cells
reports/campaigns/runs/<name>-<date>/campaign.json   the matrix and its outcomes
```

`campaign.json` is rewritten after every cell. The page's own state lives in
this process; a starting server reads every `campaign.json` back, so "Recent
campaigns" and each campaign's page (at the same address, its logs read back
from `launcher.log`, `judge-logs/` and `server-logs/` on first view) survive a
restart as the episodes do. A campaign the server stopped mid-matrix reads back
as failed, its running cell as interrupted and the cells after it as skipped.

**Resuming.** A finished campaign whose cells did not all end `done` shows
**Resume** on its page (`POST /api/campaigns/<id>/resume`). It runs again, in
place, every cell that ended `skipped` (never attempted) or `failed` (the judge
could not finish it: a lab that would not deploy, no record written, a campaign
stopped under it), with the same campaign seed and the same row seeds, into the
same directory. Every subject of the matrix therefore still faces the fault the
others faced, and the campaign's page, report and exported records read as one
campaign rather than as a first run and its restarts. Launching a new campaign
instead draws new seeds, and its subjects would no longer be compared on the
same faults as the first one's.

Cells that ended `done` are kept, including those whose subject did not repair:
running them again until they pass would be choosing the result. The earlier
attempt's record stays in the directory, but the cell now names the new one; its
judge log keeps both attempts. A campaign read back after a restart is planned
again from its request, and refused if its experiments have changed so that it
no longer plans the cells it recorded; a seed whose fault has moved is refused by
the seed register, as for any replay.

When the last cell ends, the campaign writes its own page,
`reports/campaign.report.html` (and `.json`) in that directory, and links it at the
top of the campaign's page here; each scenario in the cells table links its
episode's report. The page compares the subjects with the tables the campaign
records already use (the per-cell summary of `scripts/summarize_campaign.py`, the
evaluation parameters of `scripts/evaluation_parameters.py`, repairs by scenario
family, how the episodes ended), adds the one a matrix is for — the same plate for
every subject, and who repaired what the other did not — and then shows every
episode's interactions, rendered as its own report renders them. A page that cannot
be built is said in the launcher log and fails nothing; for a campaign the server
never finished, build it by hand:

```bash
./scripts/generate_campaign_report.py reports/campaigns/runs/<name>-<date>
```

Once that page is written, the campaign's page here draws a **Results** section
below the judge's and the subject's logs: each subject's evaluation parameters
(`scripts/evaluation_parameters.py`) as bars side by side, one colour per subject —
pass rate (the repair oracle's), tool call success, interaction limit, time limit,
repeat action, early submission and error submission rates with their 95%
interval, then the ratio of each type of action (searching counted with checking
the configuration; the report keeps the two apart). The numbers are read from the
campaign page's JSON (`/api/campaigns/<id>/parameters`), so the charts and the
page cannot disagree; a rate no episode could measure says why instead of
drawing a zero, and each chart has a table view.

Then it lays each subject's records out the way campaign records are shared
(`scripts/export_results.py`), one folder per model, campaign, subject and intent:

```
reports/campaigns/<model>/<name>-<date>/<subject>/<intent>/
  index.md                 plate -> task report, record, judge log, repair, termination
  records/                 one judge record per episode, named by plate
  logs/judge/  logs/devices/<plate>/  logs/traces/<plate>/
  reports/tasks/<plate>/   the per-task report; reports/summary.*, evaluation_parameters.json, cell.json
```

The model is named without its provider (`openai/qwen35-9b-think` ->
`qwen35-9b-think`), the subject without `_agent` (`langchain_rag_agent` ->
`langchain_rag`), and the intent is the wording the record names. The campaign's
page here links each folder's `index.md` once written. A record naming another model
than its cell is not filed. As with the page, a layout that cannot be written is said
and fails nothing; to write it by hand, or under a chosen experiment name:

```bash
./scripts/export_results.py --out reports/campaigns --campaign reports/campaigns/runs/<name>-<date> [--experiment e6_rag]
```

Three rules the runner holds to:

- **A subject is started once per subject, model and topology**, and every cell
  sharing those three runs against it — the grouping
  `experiments/run_langchain_baseline.sh` uses. The topology is in the key
  because a subject is started with its experiment's `--scenario-topology` and
  `--healthy-state`; two experiments over one topology still share a server.
- **A failed episode is counted and the matrix carries on.** A containerlab
  deployment that flaked on cell nine must not throw away cells ten to forty.
- **A record whose model provenance contradicts its cell stops everything.**
  Every cell after it would be attributed to a model that did not run. Cells
  never reached are recorded as `skipped`, never as failures: a cell that was
  not attempted has measured nothing, and putting it in the same column as a
  subject that ran and did not repair is what a campaign must not do.

**Changing topology.** The runner never calls containerlab itself: every cell is
one `benchmarks/run.py`, and its `deploy_reset_testbed` phase runs
`containerlab destroy` then `containerlab deploy` on the `.clab.yml` of that
cell's own testbed. A campaign moves from one topology to the next when the next
cell deploys a different file; each cell starts on a freshly deployed lab even
when the topology does not change. At the end of a cell the experiment's
`cleanup` decides: `destroy` removes the lab, `restore` puts the reference state
back and leaves it up. That destroy targets the cell's own file, not the previous
one, so a `restore` lab (the `*-false-positive` experiments) stays deployed when
the campaign moves on, and after it ends. The labs can coexist — each has its own
name and management subnet — but they share the host's memory and CPU;
`containerlab inspect --all` lists what is still up.

Only settings every subject accepts are offered here. A matrix that refused its
third subject over a flag the first two took would have wasted the two runs
before it. The whole matrix is compiled before anything starts, so an impossible
combination costs a second rather than the twenty minutes it takes to reach it.

An experiment file that pins `fault_applicable = false` **is** the
false-positive episode, so such a row is recorded as one whatever the box says:
the lifecycle skips injection either way, and a cell named after a fault that
was never injected is a mislabelled record.

## The seed register

A seed is not a context on its own. The compiler draws from one stream per
compilation, so the same number lands on a different fault under another
topology or after a scenario is added to the catalogue. What makes a seed
sufficient is writing down, once, what it stood for.

`reports/seed-registry.json` is that record. It is tracked by git, unlike the
run directories beside it: a seed quoted in a report is only resolvable if
whoever reads the report has the register. It holds two kinds of entry.

- An **episode** seed is the scenario seed itself. Its entry names the
  experiment, the scenario, the method and the topology, and carries the
  resolved instance — which device, which interface, the exact fault commands —
  plus a fingerprint of it.
- An **campaign** seed recalls a whole matrix: its subjects, its models, its
  scenario rows and the episode seed of every cell. Every record a campaign
  writes carries it as `provenance.seed_campaign`, so any single record leads
  back to the campaign it belonged to. One episode run on its own records
  `null` there and is identified by its scenario seed alone.

**Choosing one.** Each page has exactly one control for its seed, and they work
the same way. On the Episode page the first entry is *generate a new seed* and
the rest are the seeds the register already holds, each labelled with the fault
it stands for:

```
generate a new seed
229433172 — qos.link_impairment.m1 · external1 eth1 web1
450958128 — connectivity.disable_interface.m2 · leaf2 ethernet-1/50
```

The Campaigns page has the same control, for campaign seeds: *generate a new
campaign seed*, then each campaign the register holds.

Inside a campaign the seed belongs to the **scenario row**, not to the campaign,
and the table shows each row's in its own column — `new` for a row still to be
drawn for. It has to: a seed stands for one scenario, so two rows sharing one
would be two different faults recorded under one number, and the register
refuses the second after the first has already run. Every cell of a row shares
its seed, because subjects are compared on one fault or they are not compared at
all; and a scenario to be measured on several faults is **several rows**, each
drawn its own seed.

A new seed is drawn at launch, from a range wide enough that a collision is a
surprise, and is unique across both kinds — a seed is quoted on its own and must
not need saying which it is.

Because the menu is the register, a seed the register does not hold cannot be
picked here — including the defaults the experiment files pin (9 for
connectivity, 3 for the QoS files, 1 for filtering). Running one of those is
still what `benchmarks/run.py --seed 9` does, and the first run of any seed from
this page puts it in the menu for good.

**Replaying one.** Picking a recorded seed fills in the experiment, the scenario
and the method it was recorded with. Changing any of those three by hand
afterwards drops the choice back to *generate a new seed*: a recorded seed
stands for one scenario on one experiment, and the launcher would refuse it
against another one anyway.

Picking a recorded campaign seed does the same for a whole matrix: its name,
subjects, models, scenario rows with the seed each ran on, budget and every
advanced setting. A model no endpoint serves any more comes back as free text rather than
being dropped, because it still ran. Editing any axis afterwards drops the choice
back to a new campaign seed, for the same reason an edited episode does: a
campaign seed stands for one matrix, and the register would otherwise hold an
entry describing a matrix nobody ran. Replaying a campaign unchanged runs it
again under its own seed, and the entry gains a second campaign directory in
`runs` rather than being rewritten.

The restored seeds are the recorded ones, never fresh: a campaign replayed with
new seeds would run a different set of faults under its name. The episode is then
recompiled and compared against what was written down, before anything is
deployed. If the instance has moved, the episode is **refused** and the log
names the gap:

```
seed 749328874 was recorded as connectivity.disable_interface.m1 on
connectivity-smoke and no longer resolves to it:
interface: recorded 'ethernet-1/20', now 'ethernet-1/58'
```

A register that let that through would not be a register.

**Validating writes nothing.** The same check runs when a matrix is only
previewed, so a drifted seed is caught before anything is deployed, but nothing
is recorded: a seed written down for a campaign somebody only validated would
name a fault that was never run.

**What a seed does not fix.** It fixes the problem, not the solver: it never
reaches the subject, and nothing here seeds the model's sampling. Two cells that
differ only by subject or model deliberately share one seed and one entry —
that is what makes them comparable, and giving them different faults is exactly
what would make the comparison meaningless.

## What the form offers

The choices are read from the repository on every page load, not listed here:
subjects and their flags from `webui/catalog/architectures.py` (held to each
server's real argparse by `webui/catalog/tests/`), topologies and testbeds from
`benchmarks/configs/experiments/*.toml`, scenarios and methods from the scenario
YAMLs filtered through `scenarios/topology_applicability.json`, and prompt
variants from each subject's `prompts/` directory.

## The diagnosis judge

Evaluation parameter 13, "LLM found the problem rate", needs a second model: a judge
scores whether the subject's statement of the fault (its `diagnosis`, or its final
summary when its prompt asks for no diagnosis, as the prompts on this branch do)
names the fault the scenario injected (the ParaPLUIE score, see
`docs/diagnosis_judge.md`). The **Diagnosis judge** menu on the episode and campaign
forms offers the models registered on the Models page, none, or a model id typed by
hand, which then runs on the subject's endpoint with the subject's key, so a judge on
the same provider as the generator needs no second secret. **Judge reading** says how
the log-probabilities are read: `prompt_logprobs` (a vLLM endpoint, the exact reading)
or `top_logprobs` (the OpenAI API, which lists the candidates of the generated token;
gpt-4.1 and gpt-4o). A chosen judge is named to `benchmarks/run.py` with its endpoint's
base URL; its key reaches the judge through the environment. Every judge call is also
appended, with its raw candidates, to `diagnosis-audit.jsonl` beside the reports. The verdict table then shows `root_cause_identified`,
`diagnosis_score`, `sut_diagnosis` and `diagnosis_judge` (the model that judged) under
the model rows, and a campaign cell carries `root cause` beside its other letters. With no judge, the diagnosis is still recorded and the
rows say it was not judged.

## Models and their keys

The **Models** page registers endpoints: a provider's base URL, the key it
takes, and the model ids it serves — one entry covers every model behind a
LiteLLM or vLLM proxy. The launcher's model field then lists those models, and
keeps an "other model id…" entry for anything not registered, which runs on
whatever `.env` already defines.

Three rules the code holds to:

- **The key is never put on a command line.** The run log prints the subject's
  argv and `ps` shows it to every local user. Credentials reach the subject
  through its environment instead, under its own prefix
  (`LANGCHAIN_AGENT_API_*`),
  which its `from_env()` reads before the repository-wide `LLM_API_*` — so the
  endpoint chosen here wins over what `.env` names.
- **The key is never sent back to the browser.** A listing says whether one is
  stored, never what it is, and editing an endpoint without retyping the key
  keeps the stored one.
- **The store is `webui/models.json`**, written `0600` and listed in
  `.gitignore`. It is the only file this tool writes a secret to. Nothing stops
  you from using `.env` as before instead: an unregistered model behaves exactly
  as it did.

The SUT time budget is sent to the judge as `--execution-budget-seconds` and to
the subject as `--max-execution-seconds`: one number, so the two cannot disagree
about when an episode is over. A setting the chosen subject has no flag for is
refused rather than dropped, because a dropped setting would be recorded as one
that was applied.

**Not offered yet:** the `security` domain. No experiment file pins it to a
topology and a testbed, and its scenarios carry a task mode
(`exploit|detect|correct|prevent`) where every other domain carries a method.
Adding a `security-*.toml` and a task-mode selector is what it would take.
