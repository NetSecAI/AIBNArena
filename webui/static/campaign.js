// The matrix form. Every choice comes from window.CATALOG, the same document the
// single-episode form reads, so a scenario added to the repository appears here
// on reload too.

const catalog = window.CATALOG;
const $ = (id) => document.getElementById(id);

const NUMBERS = [
  "max_tokens", "temperature", "ani_call_limit", "recursion_limit",
];
const TEXTS = ["debug_trace", "thinking_style", "thinking_effort"];

function fill(select, options, selected) {
  select.innerHTML = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    select.append(element);
  }
  if (selected !== undefined) select.value = selected;
}

function checkboxes(container, items, checked) {
  container.innerHTML = "";
  for (const item of items) {
    const label = document.createElement("label");
    label.className = "checkbox";
    if (item.title) label.title = item.title;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = item.value;
    box.checked = checked(item);
    box.addEventListener("change", refreshSize);
    label.append(box, document.createTextNode(" " + item.label));
    container.append(label);
  }
}

function checked(container) {
  return [...container.querySelectorAll("input:checked")].map((box) => box.value);
}

// --- the scenario rows -----------------------------------------------------

// One row is one fault of the matrix, chosen the way the Episode page chooses
// one (scenario_picker.js): scenario, task wording, method, topology and
// reference state. A restored row replays on the file and the seed it ran on;
// changing any of its choices releases the seed, as on the Episode page, since
// the register would refuse that number for another instance.
const PICKERS = new WeakMap();

function addSelection(preset) {
  const row = document.createElement("div");
  row.className = "selection";
  $("selections").append(row);
  const fields = {
    scenario: document.createElement("select"),
    task: document.createElement("select"),
    method: document.createElement("select"),
    topology: document.createElement("select"),
    state: document.createElement("select"),
    noFault: document.createElement("input"),
  };
  fields.noFault.type = "checkbox";
  // A row that will draw says so; a restored row shows the number it ran on.
  const seed = document.createElement("code");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "remove";

  const picker = new ScenarioPicker(fields, {
    onEdit: () => {
      picker.release();
      showSeed(row, seed, "");
      refreshSize();
    },
  });
  PICKERS.set(row, picker);
  fields.noFault.addEventListener("change", refreshSize);
  remove.addEventListener("click", () => { row.remove(); refreshSize(); });

  // Two lines, each field under its name as on the Episode page: what runs
  // (scenario, task, method), then where and how (topology, reference state,
  // the false-positive box and the seed).
  const labelled = (caption, element) => {
    const label = document.createElement("label");
    label.append(document.createTextNode(caption), element);
    return label;
  };
  const falsePositive = document.createElement("label");
  falsePositive.className = "checkbox";
  falsePositive.append(fields.noFault, document.createTextNode("False positive"));
  const seedLine = document.createElement("span");
  seedLine.append(document.createTextNode("seed "), seed);
  const run = document.createElement("div");
  run.className = "selection-run";
  run.append(falsePositive, seedLine, remove);
  row.append(
    labelled("Scenario", fields.scenario), labelled("Task", fields.task),
    labelled("Method", fields.method), labelled("Topology", fields.topology),
    labelled("Reference state", fields.state), run);
  if (preset) picker.restore(preset);
  showSeed(row, seed, preset && preset.seed ? String(preset.seed) : "");
  return row;
}

function showSeed(row, element, value) {
  row.dataset.seed = value;
  element.textContent = value || "new";
  element.className = value ? "" : "value-muted";
}

function rowKey(row) {
  return `${row.experiment}|${row.scenario_id}|${row.no_fault}`;
}

// Every scenario and method of one domain, in one click, each on the topology
// and reference state it was written for, in the wording chosen beside the
// button (a scenario without that wording keeps its default). Twenty-one rows
// for connectivity, and each one is a cell per subject, model and seed --
// which is why the matrix size sits next to the launch button.
function domains() {
  return [...new Set(scenarioChoices().map((item) => item.domain))];
}

function addEveryMethod() {
  const domain = $("bulk-domain").value;
  const wording = $("bulk-task").value;
  const present = new Set(selections().map(rowKey));
  for (const scenario of scenarioChoices().filter((item) => item.domain === domain)) {
    const tasks = scenario.tasks || [];
    const task = tasks.find((item) => wording && wordingOf(item) === wording)
      || tasks.find((item) => item.base) || null;
    const file = possibleFiles(scenarioKey(scenario))[0];
    for (const method of scenario.methods) {
      const row = {
        experiment: file.key,
        scenario_id: instanceId(method.scenario_id, task),
        no_fault: false,
      };
      if (present.has(rowKey(row))) continue;
      present.add(rowKey(row));
      addSelection(row);
    }
  }
  refreshSize();
}

function describeBulk() {
  const rows = scenarioChoices().filter((item) => item.domain === $("bulk-domain").value)
    .reduce((total, item) => total + item.methods.length, 0);
  $("add-every").textContent = `Add all ${rows} method${rows === 1 ? "" : "s"} of this domain`;
}

function selections() {
  return [...$("selections").children].map((row) => ({
    ...PICKERS.get(row).selection(),
    // Null asks the server to draw one; only the register knows what is free.
    seed: row.dataset.seed ? Number(row.dataset.seed) : null,
  }));
}

// --- the matrix ------------------------------------------------------------

// The seeds a restored campaign ran on, kept for its note. They are not an axis:
// one row is one cell, run against every subject and model.
let restoredSeeds = [];

function models() {
  const extra = $("extra-models").value.split(",").map((item) => item.trim()).filter(Boolean);
  return [...new Set([...checked($("models")), ...extra])];
}

// An empty matrix is reported as the axis that is empty. "0 cells" is true and
// useless: with no endpoint registered yet, the axis missing is always the
// model, and the page should say so rather than leave it to be worked out.
const AXES = [
  ["subject", () => checked($("architectures")).length],
  ["model", () => models().length],
  ["scenario", () => selections().length],
];

function refreshSize() {
  const sizes = AXES.map(([name, count]) => [name, count()]);
  const empty = sizes.filter(([, count]) => count === 0).map(([name]) => name);
  const size = $("matrix-size");
  if (empty.length) {
    size.textContent = `no ${empty.join(", no ")} chosen`;
    size.className = "state failed";
  } else {
    const cells = sizes.reduce((total, [, count]) => total * count, 1);
    const seconds = cells * Number($("execution_budget_seconds").value || 0);
    size.textContent = `${cells} cell${cells === 1 ? "" : "s"} · ${budget(cells)}`;
    // Amber past a long matrix rather than a red that would read as invalid:
    // a hundred cells is a legitimate campaign, just not one to start by
    // accident after one click filled the table.
    size.className = seconds > LONG_MATRIX_SECONDS ? "state" : "state done";
    // One click can add twenty-one scenarios, and each is a cell per subject,
    // model and seed. What that costs belongs next to the launch button.
    size.title = "The episode budgets added up. A lab deployment, the oracles "
      + "and the judging are on top of it, and an episode that finishes early "
      + "gives its remainder back.";
  }
  $("campaign-note").textContent = `reports/campaigns/runs/${slug($("name").value)}-<date>/`;
}

// Past this much added-up episode budget, the matrix is worth a second look
// before launching. Four hours: longer than anyone waits at their desk.
const LONG_MATRIX_SECONDS = 4 * 3600;

function budget(cells) {
  const seconds = cells * Number($("execution_budget_seconds").value || 0);
  if (!seconds) return "no budget set";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min of subject budget`;
  return `${(seconds / 3600).toFixed(1)} h of subject budget`;
}

function slug(value) {
  return value.replace(/[^0-9A-Za-z_-]/g, "-").replace(/^-+|-+$/g, "").toLowerCase()
    || "campaign";
}

// --- the registered models ---------------------------------------------------

function registeredModels() {
  return catalog.endpoints.flatMap((endpoint) =>
    endpoint.models.map((model) => ({
      value: model,
      label: `${model} — ${endpoint.name}`,
      title: endpoint.has_key ? endpoint.api_base : `${endpoint.api_base} · no key stored`,
    })));
}

// The model checkboxes, drawn from catalog.endpoints and drawn again when the
// Models page changes them. A model still offered stays ticked. One ticked that
// no endpoint offers any more is unticked and named, and a recorded campaign's
// seed is released: the matrix on screen is no longer the one it stood for.
// The judge of every cell's diagnosis (evaluation parameter 13): none, or a
// model a registered endpoint serves. Kept while its endpoint still offers it.
const JUDGE_CUSTOM = "\u0000custom";

function fillJudges() {
  const previous = $("diagnosis-judge").value;
  const registered = registeredModels();
  fill($("diagnosis-judge"), [
    { value: "", label: "none (diagnosis recorded, not judged)" },
    ...registered.map((item) => ({ value: item.value, label: item.label })),
    { value: JUDGE_CUSTOM, label: "other model id, on each subject's endpoint…" },
  ]);
  if (previous === JUDGE_CUSTOM || registered.some((item) => item.value === previous)) $("diagnosis-judge").value = previous;
  onJudgeChoiceChange();
}

function onJudgeChoiceChange() {
  $("diagnosis-judge-custom").hidden = $("diagnosis-judge").value !== JUDGE_CUSTOM;
}

function chosenJudge() {
  const choice = $("diagnosis-judge").value;
  return choice === JUDGE_CUSTOM ? $("diagnosis_judge_model").value.trim() : choice;
}

function fillModels() {
  fillJudges();
  const ticked = checked($("models"));
  const registered = registeredModels();
  checkboxes($("models"), registered, (item) => ticked.includes(item.value));
  if (!registered.length) {
    // Nothing registered yet: an empty box with no explanation reads as a page
    // that failed to load its choices.
    const note = document.createElement("p");
    note.className = "note";
    note.innerHTML = 'No endpoint is registered yet. Add one on the '
      + '<a href="/models">Models</a> page, or name the model ids below and let '
      + '<code>.env</code> decide the key.';
    $("models").append(note);
  }
  const dropped = ticked.filter((model) => !registered.some((item) => item.value === model));
  if (dropped.length) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = `No longer registered, so unticked: ${dropped.join(", ")}.`;
    $("models").append(note);
    releaseSeed();
  }
}

// --- replaying a recorded campaign ------------------------------------------

function tick(container, values) {
  const wanted = new Set(values.map(String));
  for (const box of container.querySelectorAll("input[type=checkbox]")) {
    box.checked = wanted.has(box.value);
    wanted.delete(box.value);
  }
  // What no checkbox offers is a model no endpoint serves any more; it still
  // ran, so it is restored as free text rather than dropped.
  return [...wanted];
}

// One control for the campaign's seed, the same shape the Episode page uses for
// an episode's: a number the register already holds, or a new one. A campaign
// seed stands for the whole matrix, so restoring one restores all of it.
const NEW_SEED = "new";

function recordedCampaign() {
  return (window.SEEDS.campaigns || [])
    .find((item) => String(item.seed) === $("seed-choice").value);
}

// Set while a recorded campaign is being applied, so the fields it fills in are
// not mistaken for someone editing the matrix by hand.
let restoring = false;

function onSeedChoice() {
  const note = $("seed-note");
  const entry = recordedCampaign();
  if (!entry) {
    note.textContent = "drawn at launch, with a seed per scenario, "
      + "and written into every record";
    refreshSize();
    return;
  }
  restoring = true;
  const request = entry.request || {};

  $("name").value = request.name || "campaign";
  tick($("architectures"), request.architectures || []);
  $("extra-models").value = tick($("models"), request.models || []).join(", ");
  if (request.diagnosis_judge_model && !registeredModels().some((item) => item.value === request.diagnosis_judge_model)) {
    $("diagnosis-judge").value = JUDGE_CUSTOM;
    $("diagnosis_judge_model").value = request.diagnosis_judge_model;
  } else {
    $("diagnosis-judge").value = request.diagnosis_judge_model || "";
  }
  onJudgeChoiceChange();
  $("diagnosis_judge_reading").value = request.diagnosis_judge_reading || "";

  // The seeds of the recorded cells, not fresh ones: replaying a campaign that
  // drew new seeds would run a different set of faults under its name.
  $("execution_budget_seconds").value = request.execution_budget_seconds || 400;
  $("cleanup").value = request.cleanup || "";
  $("host").value = request.host || "127.0.0.1";
  $("port").value = request.port ?? "";
  $("dry_run").checked = Boolean(request.dry_run);
  for (const name of [...NUMBERS, ...TEXTS]) {
    $(name).value = request[name] ?? "";
  }
  $("enable_thinking").value = request.enable_thinking === undefined
    || request.enable_thinking === null ? "" : String(request.enable_thinking);

  $("selections").replaceChildren();
  restoredSeeds = (request.scenarios || []).map((row) => row.seed).filter(Boolean);
  for (const row of request.scenarios || []) {
    addSelection(row);
  }
  restoring = false;
  refreshSize();
  note.textContent = `${entry.campaign_id} · recorded ${displayTime(entry.recorded_at)}`
    + ` · seeds ${restoredSeeds.join(", ")}`;
}

// A campaign seed stands for one matrix. Editing any axis means the choice no
// longer describes what would run, and the register would end up with a seed
// whose entry describes a matrix nobody ran. So an edit drops it back to a new
// seed, exactly as changing the scenario does on the Episode page.
function releaseSeed() {
  if (restoring || !recordedCampaign()) return;
  $("seed-choice").value = NEW_SEED;
  onSeedChoice();
}

function payload() {
  const body = {
    name: $("name").value,
    architectures: checked($("architectures")),
    models: models(),
    scenarios: selections(),
    // A restored campaign runs on the seeds it recorded; a new one asks the
    // server to draw them, because only the register knows which are free.
    ...(recordedCampaign()
      ? { seed_campaign: Number($("seed-choice").value) }
      : {}),
    execution_budget_seconds: Number($("execution_budget_seconds").value),
    host: $("host").value.trim(),
    dry_run: $("dry_run").checked,
  };
  if ($("port").value) body.port = Number($("port").value);
  if ($("cleanup").value) body.cleanup = $("cleanup").value;
  if (chosenJudge()) body.diagnosis_judge_model = chosenJudge();
  if (chosenJudge() && $("diagnosis_judge_reading").value) body.diagnosis_judge_reading = $("diagnosis_judge_reading").value;
  if ($("enable_thinking").value) body.enable_thinking = $("enable_thinking").value === "true";
  for (const name of NUMBERS) if ($(name).value !== "") body[name] = Number($(name).value);
  for (const name of TEXTS) if ($(name).value !== "") body[name] = $(name).value.trim();
  return body;
}

async function post(path) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload()),
  });
  return { ok: response.ok, status: response.status, body: await response.json() };
}

function report(result) {
  const feedback = $("feedback");
  feedback.hidden = false;
  feedback.classList.toggle("error", !result.ok);
  feedback.textContent = result.ok
    ? JSON.stringify(result.body, null, 2)
    : `${result.status}: ${JSON.stringify(result.body.detail ?? result.body, null, 2)}`;
}

$("add-selection").addEventListener("click", () => { addSelection(); refreshSize(); });
$("add-every").addEventListener("click", addEveryMethod);
$("bulk-domain").addEventListener("change", describeBulk);
$("diagnosis-judge").addEventListener("change", onJudgeChoiceChange);
$("seed-choice").addEventListener("change", onSeedChoice);
// Every axis of the matrix lives in this form, so one listener covers them all.
// The seed select is skipped: choosing a campaign is not editing one.
for (const event of ["change", "input"]) {
  $("campaign").addEventListener(event, (moment) => {
    if (moment.target.id !== "seed-choice") releaseSeed();
  });
}
for (const id of ["name", "extra-models", "execution_budget_seconds"]) {
  $(id).addEventListener("input", refreshSize);
}

$("validate").addEventListener("click", async () => {
  $("validate").disabled = true;
  try {
    report(await post("/api/campaigns/validate"));
  } finally {
    $("validate").disabled = false;
  }
});

$("campaign").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("launch").disabled = true;
  const result = await post("/api/campaigns");
  if (result.ok) {
    window.location.href = `/campaign/${result.body.id}`;
    return;
  }
  report(result);
  $("launch").disabled = false;
});

checkboxes($("architectures"), catalog.architectures.map((item) => ({
  value: item.key, label: item.label, title: `${item.default_model} by default, port ${item.default_port}`,
})), (item) => item.value === catalog.architectures[0].key);

fillModels();

fill($("bulk-domain"), domains().map((domain) => ({ value: domain, label: domain })));
fill($("bulk-task"), [
  { value: "", label: "each scenario's default wording" },
  ...BY_DIFFICULTY.map((variant) => ({
    value: variant, label: `${DIFFICULTY[variant]} — ${variant} precision`,
  })),
]);
fill($("seed-choice"), [
  { value: NEW_SEED, label: "generate a new campaign seed" },
  ...(window.SEEDS.campaigns || []).map((entry) => ({
    value: String(entry.seed),
    label: `${entry.seed} — ${entry.campaign_id}`
      + ` · ${(entry.episode_seeds || []).length} seeds`,
  })),
]);
describeBulk();
addSelection();
onSeedChoice();
followEndpoints(catalog.endpoints, (endpoints) => {
  catalog.endpoints = endpoints;
  fillModels();
  refreshSize();
});
