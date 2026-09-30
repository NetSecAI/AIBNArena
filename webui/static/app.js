// The form. Every choice it offers comes from window.CATALOG, which the server
// derives from the experiment files, the scenario YAMLs and the subjects
// themselves -- so a scenario added to the repository appears here on reload.

const catalog = window.CATALOG;
const $ = (id) => document.getElementById(id);

const NUMBERS = [
  "max_tokens", "temperature", "ani_call_limit",
  "min_retry_tokens", "tool_result_chars", "context_budget_chars",
  "max_context_chars", "max_consecutive_rejections", "recursion_limit",
];
const TEXTS = [
  "prompt_variant", "debug_trace", "artifact_directory", "thinking_style", "thinking_effort",
];

function architecture() {
  return catalog.architectures.find((item) => item.key === $("architecture").value);
}

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

// The model list is the registered endpoints' models, plus the free-text entry
// that keeps running whatever the environment already has a key for.
const CUSTOM = "\u0000custom";

function registeredModels() {
  return catalog.endpoints.flatMap((endpoint) =>
    endpoint.models.map((model) => ({ model, endpoint })));
}

function onModelChoiceChange() {
  const custom = $("model-choice").value === CUSTOM;
  $("model-custom").hidden = !custom;
  const chosen = registeredModels().find((item) => item.model === $("model-choice").value);
  $("model-note").textContent = chosen
    ? `${chosen.endpoint.name} · ${chosen.endpoint.api_base}` +
      (chosen.endpoint.has_key ? "" : " · no key stored")
    : "";
}

// The model select, drawn from catalog.endpoints and drawn again when the Models
// page changes them. The model chosen here stays chosen while an endpoint still
// offers it. One that went away is replaced by the first offered, and the note
// names it, so the launcher never switches models without a word.
// The judge of the subject's diagnosis (evaluation parameter 13): none, or a
// model a registered endpoint serves. Drawn with the model list and kept while
// its endpoint still offers it.
function fillJudges() {
  const previous = $("diagnosis-judge").value;
  const offered = registeredModels();
  fill($("diagnosis-judge"), [
    { value: "", label: "none (diagnosis recorded, not judged)" },
    ...offered.map((item) => ({
      value: item.model,
      label: `${item.model} — ${item.endpoint.name}`,
    })),
    { value: CUSTOM, label: "other model id, on the subject's endpoint…" },
  ]);
  if (previous === CUSTOM || offered.some((item) => item.model === previous)) $("diagnosis-judge").value = previous;
  onJudgeChoiceChange();
}

function onJudgeChoiceChange() {
  $("diagnosis-judge-custom").hidden = $("diagnosis-judge").value !== CUSTOM;
}

function chosenJudge() {
  const choice = $("diagnosis-judge").value;
  return choice === CUSTOM ? $("diagnosis_judge_model").value.trim() : choice;
}

function fillModels() {
  fillJudges();
  const previous = $("model-choice").value;
  const offered = registeredModels();
  fill($("model-choice"), [
    ...offered.map((item) => ({
      value: item.model,
      label: `${item.model} — ${item.endpoint.name}`,
    })),
    { value: CUSTOM, label: "other model id…" },
  ]);
  const kept = previous === CUSTOM || offered.some((item) => item.model === previous);
  if (kept) $("model-choice").value = previous;
  onModelChoiceChange();
  if (previous && !kept) {
    $("model-note").textContent = [`${previous} is no longer registered`,
      $("model-note").textContent].filter(Boolean).join(" · ");
  }
}

function chosenModel() {
  const choice = $("model-choice").value;
  return choice === CUSTOM ? $("model").value.trim() : choice;
}

function onArchitectureChange() {
  const chosen = architecture();
  $("model").value = chosen.default_model;
  $("port").placeholder = chosen.default_port;
  fill($("prompt_variant"), chosen.prompt_variants.map((name) => ({ value: name, label: name })));
  // A field the chosen subject has no flag for is hidden rather than sent: the
  // server refuses it, because a dropped setting would be recorded as applied.
  for (const label of document.querySelectorAll("[data-flag]")) {
    const supported = chosen.flags.includes(label.dataset.flag);
    label.hidden = !supported;
    if (!supported) label.querySelector("input, select").value = "";
  }
}

// The scenario, its task, method, topology and reference state are chosen by a
// ScenarioPicker (scenario_picker.js), the same one every campaign row uses.

// --- seeds -----------------------------------------------------------------

// One control for the seed: a number the register already holds, or a new one.
// A menu entry says which fault it runs, not just its number -- a list of bare
// seeds is a list nobody can choose from.
const NEW_SEED = "new";

function seedLabel(entry) {
  return `${entry.seed} — ${entry.scenario_id}`
    + (entry.no_fault || isFalsePositiveFile(entry.experiment) ? " (false positive)" : "")
    + (entry.instance ? ` · ${entry.instance}` : "");
}

function recordedSeed() {
  return (window.SEEDS.episodes || [])
    .find((item) => String(item.seed) === $("seed-choice").value);
}

// Set while a recorded seed is being applied, so the scenario changes it makes
// are not mistaken for someone choosing a different scenario by hand.
let restoring = false;

function onSeedChoice() {
  const note = $("seed-note");
  const entry = recordedSeed();
  if (!entry) {
    note.textContent = "drawn at launch and written to the register";
    // What was offered for a recorded seed goes with that seed.
    picker.release();
    return;
  }
  restoring = true;
  // The scenario first: changing it rebuilds the method list and narrows the
  // files to the ones it applies to, then the file the entry was recorded on is
  // chosen among them, and what else the entry recorded is applied after that.
  picker.restore(entry);
  restoring = false;
  note.textContent = `recorded ${displayTime(entry.recorded_at)}`
    + (entry.runs ? ` · run ${entry.runs} time${entry.runs === 1 ? "" : "s"}` : "")
    + (entry.seed_campaign ? ` · from campaign ${entry.seed_campaign}` : "");
}

// A recorded seed stands for one scenario on one experiment. Changing either by
// hand means the choice no longer describes what would run, so it falls back to
// a new seed rather than quietly running the old number against a new context --
// which the launcher would refuse anyway.
function releaseSeed() {
  if (restoring || !recordedSeed()) return;
  $("seed-choice").value = NEW_SEED;
  onSeedChoice();
}

function payload() {
  const body = {
    architecture: $("architecture").value,
    ...picker.selection(),
    model: chosenModel(),
    ...(recordedSeed()
      ? { seed: Number($("seed-choice").value) }
      : { new_seed: true }),
    execution_budget_seconds: Number($("execution_budget_seconds").value),
    dry_run: $("dry_run").checked,
    host: $("host").value.trim(),
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

$("seed-choice").addEventListener("change", onSeedChoice);
$("architecture").addEventListener("change", onArchitectureChange);
$("model-choice").addEventListener("change", onModelChoiceChange);
$("diagnosis-judge").addEventListener("change", onJudgeChoiceChange);

$("validate").addEventListener("click", async () => {
  $("validate").disabled = true;
  try {
    report(await post("/api/validate"));
  } finally {
    $("validate").disabled = false;
  }
});

$("episode").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("launch").disabled = true;
  const result = await post("/api/runs");
  if (result.ok) {
    window.location.href = `/run/${result.body.id}`;
    return;
  }
  report(result);
  $("launch").disabled = false;
});

fillModels();
fill($("architecture"), catalog.architectures.map((item) => ({ value: item.key, label: item.label })));
fill($("seed-choice"), [
  { value: NEW_SEED, label: "generate a new seed" },
  ...(window.SEEDS.episodes || []).map((entry) => ({
    value: String(entry.seed), label: seedLabel(entry),
  })),
]);
onArchitectureChange();
const picker = new ScenarioPicker({
  scenario: $("scenario"), task: $("task"), taskNote: $("task-note"), taskField: $("task-field"),
  method: $("method"), topology: $("topology"),
  state: $("experiment"), stateNote: $("experiment-note"), noFault: $("no_fault"),
}, {
  // Another file brings its own budget, as choosing one by hand always did.
  onFileChange: (file) => { $("execution_budget_seconds").value = file.execution_budget_seconds; },
  // A recorded seed stands for what it was written down as; any change releases it.
  onEdit: releaseSeed,
});
onSeedChoice();
followEndpoints(catalog.endpoints, (endpoints) => {
  catalog.endpoints = endpoints;
  fillModels();
});
