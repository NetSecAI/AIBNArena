// The live view. The stream replays the run from its first line before it
// carries new ones, so this page reads the same whether the episode is running
// or long finished.

// One pane per voice: the judge and the subject run at the same time and are
// read side by side, and the launcher's own narration sits above both.
const PANES = {
  launcher: document.getElementById("log-launcher"),
  benchmark: document.getElementById("log-benchmark"),
  sut: document.getElementById("log-sut"),
};
const state = document.getElementById("state");

function append(line, source) {
  // An unnamed line is the launcher's: it is the only voice that is not a
  // process, so a line with nothing to attribute it to belongs there.
  const pane = PANES[source] || PANES.launcher;
  const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
  pane.textContent += line + "\n";
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

function setState(name) {
  state.textContent = name;
  state.className = `state ${name}`;
}

function renderVerdict(run) {
  const section = document.getElementById("verdict");
  const table = document.getElementById("verdict-table");
  const rows = run.error ? [["error", run.error]] : [];
  for (const [key, value] of Object.entries(run.verdict || {})) {
    rows.push([key, value]);
  }
  if (!rows.length) return;
  table.innerHTML = "";
  for (const [key, value] of rows) {
    const row = table.insertRow();
    row.insertCell().textContent = key;
    const cell = row.insertCell();
    cell.textContent = String(value);
    if (value === true) cell.className = "value-true";
    if (value === false) cell.className = "value-false";
  }
  const artifacts = document.getElementById("artifacts");
  artifacts.textContent = "";
  for (const path of [run.result_path, run.report_path]) {
    if (!path) continue;
    artifacts.append(reportLink(path), document.createElement("br"));
  }
  section.hidden = false;
}

const stream = new EventSource(`/api/runs/${window.RUN.id}/events`);
stream.onmessage = (message) => {
  const event = JSON.parse(message.data);
  if (event.type === "log") append(event.line, event.source);
  if (event.type === "state") setState(event.state);
  if (event.type === "finished") {
    setState(event.state);
    renderVerdict(event);
    stream.close();
  }
};
stream.onerror = () => stream.close();
