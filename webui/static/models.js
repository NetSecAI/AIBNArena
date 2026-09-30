// The endpoints page. It never receives a stored key, only whether one is set,
// so editing an endpoint leaves its key alone unless a new one is typed.

const $ = (id) => document.getElementById(id);
const body = document.querySelector("#endpoints tbody");

function report(message, failed) {
  const feedback = $("feedback");
  feedback.hidden = false;
  feedback.classList.toggle("error", Boolean(failed));
  feedback.textContent = message;
}

function edit(endpoint) {
  $("form-title").textContent = `Edit ${endpoint.name}`;
  $("id").value = endpoint.id;
  $("name").value = endpoint.name;
  $("api_base").value = endpoint.api_base;
  $("models").value = endpoint.models.join(", ");
  $("api_key").value = "";
  $("key-note").textContent = endpoint.has_key
    ? "A key is stored. Type a new one to replace it."
    : "No key stored for this endpoint.";
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function clearForm() {
  $("form-title").textContent = "Add an endpoint";
  $("endpoint").reset();
  $("id").value = "";
  $("key-note").textContent = "";
  $("feedback").hidden = true;
}

function row(endpoint) {
  const line = body.insertRow();
  line.insertCell().textContent = endpoint.name;
  line.insertCell().textContent = endpoint.api_base;
  const key = line.insertCell();
  key.textContent = endpoint.has_key ? "set" : "none";
  key.className = endpoint.has_key ? "value-true" : "value-false";
  line.insertCell().textContent = endpoint.models.join(", ") || "—";
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

$("endpoint").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: $("name").value,
    api_base: $("api_base").value,
    models: $("models").value.split(",").map((model) => model.trim()).filter(Boolean),
  };
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

refresh();
