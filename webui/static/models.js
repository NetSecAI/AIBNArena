// The endpoints page. It never receives a stored key, only whether one is set,
// so editing an endpoint leaves its key alone unless a new one is typed.
//
// An endpoint is of one of two types. OpenAI's own API has a fixed URL, so the
// form asks for none, and its models are chosen from those the key can use, which
// the server lists (/api/openai/models) and never answers with the key. Any other
// OpenAI-compatible endpoint is given by its base URL and its model ids typed.

const $ = (id) => document.getElementById(id);
const body = document.querySelector("#endpoints tbody");
const PROVIDER_LABELS = { openai: "OpenAI", custom: "compatible endpoint" };

function report(message, failed) {
  const feedback = $("feedback");
  feedback.hidden = false;
  feedback.classList.toggle("error", Boolean(failed));
  feedback.textContent = message;
}

// An OpenAI model is stored as litellm takes it, `openai/<id>`; the form shows the id.
const bare = (model) => model.replace(/^openai\//, "");
const typed = () => $("models").value.split(",").map((model) => model.trim()).filter(Boolean);

// --- how each model thinks ----------------------------------------------------
// Chosen here and nowhere else. The draft is keyed by the model id as the Models
// field shows it, and only the models in that field are sent.
const THINKING_CHOICES = {
  enable_thinking: [["", "model default"], ["true", "on"], ["false", "off"]],
  thinking_effort: [["", "unset"], ["minimal", "minimal"], ["low", "low"],
    ["medium", "medium"], ["high", "high"]],
  thinking_style: [["", "unset"], ["auto", "auto"], ["chat_template", "chat_template"],
    ["system_prompt", "system_prompt"]],
};
let thinkingDraft = {};

function thinkingSelect(model, field) {
  const select = document.createElement("select");
  for (const [value, label] of THINKING_CHOICES[field]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
  const current = (thinkingDraft[model] || {})[field];
  select.value = current === undefined || current === null ? "" : String(current);
  select.addEventListener("change", () => {
    const settings = { ...(thinkingDraft[model] || {}) };
    if (select.value === "") delete settings[field];
    else settings[field] = field === "enable_thinking" ? select.value === "true" : select.value;
    thinkingDraft[model] = settings;
    drawThinking();
  });
  return select;
}

function drawThinking() {
  const rows = $("thinking-table").querySelector("tbody");
  rows.replaceChildren();
  const models = typed();
  for (const model of models) {
    const line = rows.insertRow();
    line.insertCell().textContent = model;
    line.insertCell().append(thinkingSelect(model, "enable_thinking"));
    const effort = thinkingSelect(model, "thinking_effort");
    // An effort level only means something with thinking on: off sends the family's own
    // "off" level, and the model's default sends nothing.
    effort.disabled = (thinkingDraft[model] || {}).enable_thinking !== true;
    line.insertCell().append(effort);
    line.insertCell().append(thinkingSelect(model, "thinking_style"));
  }
  $("thinking-table").hidden = !models.length;
  $("thinking-note").textContent = models.length
    ? "Saved with the endpoint, and checked against each model's family when it is."
    : "Type or tick a model above to choose how it thinks.";
}

function thinkingPayload() {
  const payload = {};
  for (const model of typed()) {
    const settings = { ...(thinkingDraft[model] || {}) };
    if (settings.enable_thinking !== true) delete settings.thinking_effort;
    if (Object.keys(settings).length) payload[model] = settings;
  }
  return payload;
}

function showProvider() {
  const openai = $("provider").value === "openai";
  $("api-base-field").hidden = openai;
  $("api_base").required = !openai;
  $("name").required = !openai;
  $("name").placeholder = openai ? "OpenAI" : "internal LiteLLM proxy";
  $("models").placeholder = openai ? "gpt-5.4, gpt-4o-mini" : "openai/gpt-5.4, openai/gpt-4o-mini";
  $("models-note").textContent = openai
    ? "OpenAI model ids, comma separated, or ticked below. The launcher offers each as openai/<id>."
    : "Comma-separated model ids, exactly as litellm takes them.";
  $("openai-choice").hidden = !openai;
}

function tickTyped() {
  const chosen = new Set(typed().map(bare));
  for (const box of $("openai-models").querySelectorAll("input[type=checkbox]")) {
    box.checked = chosen.has(box.value);
  }
}

function toggle(model, wanted) {
  const others = typed().filter((item) => bare(item) !== model);
  $("models").value = (wanted ? [...others, model] : others).join(", ");
  drawThinking();
}

function drawModels(models) {
  const container = $("openai-models");
  container.replaceChildren();
  for (const model of models) {
    const label = document.createElement("label");
    label.className = "checkbox";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = model;
    box.addEventListener("change", () => toggle(model, box.checked));
    label.append(box, document.createTextNode(model));
    container.append(label);
  }
  tickTyped();
}

async function listModels() {
  const payload = {};
  if ($("api_key").value) payload.api_key = $("api_key").value;
  else if ($("id").value) payload.id = $("id").value;
  const note = $("list-note");
  note.classList.remove("error");
  if (!payload.api_key && !payload.id) {
    note.textContent = "Type the API key first: the models listed are the ones it can use.";
    note.classList.add("error");
    return;
  }
  note.textContent = "Asking OpenAI…";
  let response;
  let answer;
  try {
    response = await fetch("/api/openai/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    answer = await response.json();
  } catch (error) {
    note.textContent = `The server did not answer: ${error}`;
    note.classList.add("error");
    return;
  }
  if (!response.ok) {
    note.textContent = `${response.status}: ${answer.detail}`;
    note.classList.add("error");
    return;
  }
  drawModels(answer.models);
  note.textContent = answer.models.length
    ? `${answer.models.length} chat models this key can use. Ticking one adds it to the Models field.`
    : "OpenAI lists no chat model for this key.";
}

function edit(endpoint) {
  $("form-title").textContent = `Edit ${endpoint.name}`;
  $("id").value = endpoint.id;
  $("provider").value = endpoint.provider || "custom";
  $("name").value = endpoint.name;
  $("api_base").value = endpoint.provider === "openai" ? "" : endpoint.api_base;
  $("models").value = (endpoint.provider === "openai"
    ? endpoint.models.map(bare) : endpoint.models).join(", ");
  // Keyed as the field shows the models: an OpenAI model without its openai/ prefix.
  thinkingDraft = Object.fromEntries(Object.entries(endpoint.thinking || {}).map(
    ([model, settings]) => [endpoint.provider === "openai" ? bare(model) : model, { ...settings }]));
  drawThinking();
  $("api_key").value = "";
  $("key-note").textContent = endpoint.has_key
    ? "A key is stored. Type a new one to replace it."
    : "No key stored for this endpoint.";
  $("openai-models").replaceChildren();
  $("list-note").textContent = "";
  showProvider();
  if (endpoint.provider === "openai" && endpoint.has_key) listModels();
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function clearForm() {
  $("form-title").textContent = "Add an endpoint";
  $("endpoint").reset();
  $("id").value = "";
  $("key-note").textContent = "";
  $("feedback").hidden = true;
  $("openai-models").replaceChildren();
  $("list-note").classList.remove("error");
  $("list-note").textContent =
    "OpenAI's chat models this key can use. Ticking one adds it to the Models field.";
  thinkingDraft = {};
  drawThinking();
  showProvider();
}

function row(endpoint) {
  const line = body.insertRow();
  line.insertCell().textContent = endpoint.name;
  line.insertCell().textContent = PROVIDER_LABELS[endpoint.provider] || endpoint.provider;
  line.insertCell().textContent = endpoint.api_base;
  const key = line.insertCell();
  key.textContent = endpoint.has_key ? "set" : "none";
  key.className = endpoint.has_key ? "value-true" : "value-false";
  line.insertCell().textContent = endpoint.models
    .map((model) => `${model} (${describeThinking(thinkingOf(endpoint, model))})`)
    .join(", ") || "—";
  const actions = line.insertCell();

  const change = document.createElement("button");
  change.type = "button";
  change.textContent = "Edit";
  change.addEventListener("click", () => edit(endpoint));

  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Delete";
  remove.addEventListener("click", async () => {
    if (!window.confirm(`Delete ${endpoint.name} and its stored key?`)) return;
    const response = await fetch(`/api/endpoints/${encodeURIComponent(endpoint.id)}`,
      { method: "DELETE" });
    if (!response.ok) {
      report(`${response.status}: ${(await response.json()).detail}`, true);
      return;
    }
    if ($("id").value === endpoint.id) clearForm();
    announceEndpointsChanged();
    await refresh();
  });

  actions.append(change, remove);
}

async function refresh() {
  const document_ = await (await fetch("/api/endpoints")).json();
  body.innerHTML = "";
  for (const endpoint of document_.endpoints) row(endpoint);
}

$("reset").addEventListener("click", clearForm);
$("provider").addEventListener("change", showProvider);
$("models").addEventListener("input", () => { tickTyped(); drawThinking(); });
$("list-models").addEventListener("click", listModels);

$("endpoint").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    provider: $("provider").value,
    name: $("name").value,
    models: typed(),
    thinking: thinkingPayload(),
  };
  if (payload.provider === "custom") payload.api_base = $("api_base").value;
  if ($("id").value) payload.id = $("id").value;
  // Omitted rather than empty: an empty key would clear the stored one.
  if ($("api_key").value) payload.api_key = $("api_key").value;

  const response = await fetch("/api/endpoints", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const answer = await response.json();
  if (!response.ok) {
    report(`${response.status}: ${answer.detail}`, true);
    return;
  }
  clearForm();
  report(`Saved ${answer.name}.`, false);
  // The Episode and Campaign pages open in other tabs redraw their model lists.
  announceEndpointsChanged();
  await refresh();
});

showProvider();
drawThinking();
refresh();
