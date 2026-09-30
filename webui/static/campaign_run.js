// The live view of a matrix. Same stream as one episode -- replayed from its
// first line, then followed -- with one more event type: a cell whose state or
// verdict changed.

const PANES = {
  launcher: document.getElementById("log-launcher"),
  benchmark: document.getElementById("log-benchmark"),
  sut: document.getElementById("log-sut"),
};
const state = document.getElementById("state");
const body = document.querySelector("#cells tbody");
const rows = new Map();

function append(line, source) {
  const pane = PANES[source] || PANES.launcher;
  const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
  pane.textContent += line + "\n";
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

function setState(name) {
  state.textContent = name;
  state.className = `state ${name}`;
}

// A cell's verdict is PASS only when every check the judge makes held: the
// network repaired, converged, and the subject concluding that it repaired it
// and verified it. The matrix is read by scanning that column, so it says PASS
// or FAIL first, then only what failed, in words; the raw fields are in the
// tooltip.
function failedChecks(verdict) {
  const reasons = [];
  if (verdict.lifecycle_status && verdict.lifecycle_status !== "COMPLETED") {
    reasons.push(`judge did not complete (${verdict.lifecycle_status.toLowerCase()})`);
  }
  if (verdict.environment_success === false) reasons.push("network not repaired");
  if (verdict.converged === false) reasons.push("not converged");
  if (verdict.sut_status && verdict.sut_status !== "COMPLETED") {
    reasons.push(verdict.sut_status === "FAILED"
      ? "agent reported failure" : `agent ended ${verdict.sut_status.toLowerCase()}`);
  } else if (verdict.sut_verified === false) {
    // A subject that gave up did not verify either; saying so twice is noise.
    reasons.push("agent did not verify its fix");
  }
  return reasons;
}

function verdictCell(cell) {
  const holder = document.createElement("span");
  if (cell.error) {
    holder.textContent = cell.error;
    holder.className = "value-false";
    return holder;
  }
  const verdict = cell.verdict;
  if (!verdict || typeof verdict.benchmark_success !== "boolean") return holder;
  const passed = verdict.benchmark_success;
  const badge = document.createElement("strong");
  badge.textContent = passed ? "✓ PASS" : "✗ FAIL";
  badge.className = passed ? "value-true" : "value-false";
  holder.append(badge);
  const reasons = passed ? [] : failedChecks(verdict);
  if (reasons.length) {
    holder.append(" ", Object.assign(document.createElement("span"), {
      className: "verdict-why", textContent: reasons.join(" · "),
    }));
  }
  // Evaluation parameter 13, judged apart from PASS/FAIL: whether the agent named the
  // injected fault. Its own word, so a failed repair with the right diagnosis still
  // reads as such (and a repaired network with a wrong story too).
  if (typeof verdict.root_cause_identified === "boolean") {
    const found = verdict.root_cause_identified;
    holder.append(" ", Object.assign(document.createElement("span"), {
      className: found ? "value-true" : "value-false",
      textContent: found ? "root cause found" : "root cause missed",
    }));
  }
  holder.title = ["benchmark_success", "environment_success", "converged", "sut_status", "sut_verified",
    "root_cause_identified", "diagnosis_score", "diagnosis_judge"]
    .filter((field) => verdict[field] !== undefined && verdict[field] !== null)
    .map((field) => `${field}: ${verdict[field]}`)
    .join("\n");
  return holder;
}

function renderCell(cell) {
  let row = rows.get(cell.index);
  if (!row) {
    row = body.insertRow();
    rows.set(cell.index, row);
    for (let column = 0; column < 8; column += 1) row.insertCell();
  }
  const scenario = cell.report_path
    ? reportLink(cell.report_path, cell.scenario_id)
    : document.createTextNode(cell.scenario_id);
  row.cells[0].textContent = cell.index + 1;
  row.cells[1].textContent = cell.architecture;
  row.cells[2].textContent = cell.model;
  row.cells[3].replaceChildren(scenario);
  row.cells[3].title = cell.experiment;
  row.cells[4].textContent = cell.seed;
  row.cells[5].textContent = cell.no_fault ? "false positive" : "injected";
  row.cells[6].replaceChildren(Object.assign(document.createElement("span"), {
    textContent: cell.state, className: `state ${cell.state}`,
  }));
  row.cells[7].replaceChildren(verdictCell(cell));
  row.dataset.state = cell.state;
}

// Counted from the rows rather than from the server's tally: a cell event
// carries the cell, not the totals, and a header that only refreshed when the
// matrix ended would read "nothing yet" for the whole run.
function renderCounts() {
  const tally = new Map();
  for (const cell of rows.values()) {
    tally.set(cell.dataset.state, (tally.get(cell.dataset.state) || 0) + 1);
  }
  const parts = [...tally].map(([name, value]) => `${value} ${name}`);
  document.getElementById("counts").textContent =
    `${parts.join(" · ")} of ${rows.size}`;
}

// The campaign's own page: the subjects compared, then every episode's
// interactions. Written once the last cell ends, so it is linked from then on.
function showReport(run) {
  if (!run.report_path) return;
  document.getElementById("campaign-report").replaceChildren(reportLink(run.report_path));
}

// Each subject's records, laid out as <model>/<campaign>/<subject>/<intent>/ when the
// campaign ends. When that could not be written, the command that writes it.
function showExport(run) {
  const note = document.getElementById("export");
  const paths = run.export_paths || [];
  if (paths.length) {
    note.replaceChildren("Records laid out per subject: ");
    paths.forEach((path, index) => {
      if (index) note.append(" · ");
      note.append(reportLink(`${path}/index.md`, `${path}/`));
    });
  } else {
    note.textContent =
      `Lay these records out with: scripts/export_results.py --out reports/campaigns `
      + `--campaign ${run.result_dir}`;
  }
  note.hidden = false;
}

// A finished campaign whose cells did not all end done is resumed from here: those
// cells run again in this campaign, on the seeds they were planned with, and the
// page then follows them like any campaign in flight.
function showResume(run) {
  const finished = run.state === "done" || run.state === "failed";
  const left = finished ? run.cells.filter((cell) => cell.state !== "done").length : 0;
  document.getElementById("resume").hidden = !left;
  if (left) {
    document.getElementById("resume-button").textContent =
      `Resume: run the ${left} cell${left === 1 ? "" : "s"} not done`;
  }
}

document.getElementById("resume-button").addEventListener("click", async () => {
  const button = document.getElementById("resume-button");
  button.disabled = true;
  const response = await fetch(`/api/campaigns/${window.CAMPAIGN.id}/resume`, { method: "POST" });
  if (response.ok) {
    location.reload();
    return;
  }
  const body = await response.json().catch(() => ({}));
  const note = document.getElementById("resume-note");
  note.textContent = `${response.status}: ${body.detail ?? "the campaign was not resumed"}`;
  note.classList.add("value-false");
  button.disabled = false;
});

for (const cell of window.CAMPAIGN.cells) renderCell(cell);
renderCounts();
showReport(window.CAMPAIGN);
showResume(window.CAMPAIGN);

const stream = new EventSource(`/api/campaigns/${window.CAMPAIGN.id}/events`);
stream.onmessage = (message) => {
  const event = JSON.parse(message.data);
  if (event.type === "log") append(event.line, event.source);
  if (event.type === "state") setState(event.state);
  if (event.type === "cell") { renderCell(event.cell); renderCounts(); }
  if (event.type === "finished") {
    setState(event.state);
    for (const cell of event.cells) renderCell(cell);
    renderCounts();
    showReport(event);
    showExport(event);
    showResume(event);
    renderCampaignCharts(event);
    stream.close();
  }
};
stream.onerror = () => stream.close();
