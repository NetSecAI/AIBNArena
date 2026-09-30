// The campaign's results, charted once it has ended: each subject's evaluation
// parameters as the campaign's report computed them (scripts/evaluation_parameters.py
// through scripts/generate_campaign_report.py), so the page and the report cannot
// disagree. One bar per subject, in the matrix's order and in the same colour in
// both charts; a parameter a subject was never measured on says why instead of
// drawing a zero.

const CHART_RATES = [
  ["pass_rate", "Pass rate", "higher is better", "repair oracle"],
  ["tool_call_success_rate", "Tool call success rate", "higher is better"],
  ["interaction_limit_rate", "Interaction limit rate", "lower is better"],
  ["time_limit_rate", "Time limit rate", "lower is better"],
  ["repeat_action_rate", "Repeat action rate", "lower is better"],
  ["early_submission_rate", "Early submission rate", "lower is better"],
  ["error_submission_rate", "Error submission rate", "lower is better"],
  // Evaluation parameter 13: the ParaPLUIE judge's verdict on whether the subject
  // named the injected fault, over the episodes it judged.
  ["llm_found_problem_rate", "Root cause identified", "higher is better", "ParaPLUIE judge"],
];

const CHART_ACTIONS = [
  ["check_config", "Check config", "includes search"],
  ["apply_config", "Apply config"],
  ["wait", "Wait"],
  ["validate", "Validate"],
  ["unknown", "Unknown"],
];

// Categorical slots, in a fixed order (app.css --series-1..8). A ninth subject gets
// no generated colour: it stays in the campaign's report.
const CHART_SLOTS = 8;
const CHART_TICKS = [0, 0.25, 0.5, 0.75, 1];

function chartNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The 95% Wilson interval, as evaluation_parameters.wilson_interval computes it.
function chartWilson(successes, n, z = 1.96) {
  if (n <= 0) return [0, 0];
  const p = successes / n;
  const denominator = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denominator;
  const spread = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denominator;
  return [Math.max(0, centre - spread), Math.min(1, centre + spread)];
}

const chartPercent = (value) => `${Math.round(value * 100)}%`;

// Searching is reading the network's configuration, so this page folds the report's
// `search` share into `check_config` rather than drawing it as a row of its own. On
// the ANI it is always structurally zero and changes nothing; a surface that offers
// a search operation has its calls counted here. The report keeps them apart.
function checkConfigEntry(ratio) {
  const check = ratio.check_config;
  const search = ratio.search;
  if (!check || check.status !== "measured" || !search || search.status !== "measured"
      || search.denominator !== check.denominator) {
    return check;
  }
  const numerator = check.numerator + search.numerator;
  return {
    ...check,
    numerator,
    value: check.denominator ? numerator / check.denominator : 0,
    definition: `${check.definition || ""} (search calls counted here too)`.trim(),
  };
}

// What one entry says, in words: the value when it was measured, why not otherwise.
function chartReading(entry, withInterval) {
  if (!entry || typeof entry !== "object") return { value: "–", missing: "not in the report" };
  if (entry.error) return { value: "–", missing: entry.error };
  if (entry.status === "measured") {
    const reading = {
      value: `${chartPercent(entry.value)} · ${entry.numerator}/${entry.denominator}`,
      share: entry.value,
    };
    if (withInterval && entry.denominator) {
      reading.interval = chartWilson(entry.numerator, entry.denominator);
    }
    return reading;
  }
  if (entry.status === "structurally_zero") {
    // Zero because the surface has no such operation, not because the subject chose
    // none: said, not drawn as a measured zero.
    return { value: `0% · 0/${entry.denominator}`, missing: `none possible: ${entry.reason}`,
             structural: true };
  }
  const label = entry.status === "no_denominator" ? "not measured" : "not available";
  return { value: "–", missing: `${label}: ${entry.reason || "no reason recorded"}` };
}

function chartTip() {
  let tip = document.getElementById("chart-tip");
  if (!tip) {
    tip = chartNode("div", "chart-tip");
    tip.id = "chart-tip";
    tip.hidden = true;
    document.body.append(tip);
  }
  return tip;
}

function showChartTip(row, lines, colour) {
  const tip = chartTip();
  tip.replaceChildren();
  const [value, [, series], ...rest] = lines;
  tip.append(chartNode("strong", "", value));
  if (series) {
    const line = chartNode("div", "chart-tip-series");
    const key = chartNode("span", "chart-tip-key");
    key.style.background = colour || "var(--muted)";
    line.append(key, document.createTextNode(series));
    tip.append(line);
  }
  for (const [className, text] of rest) {
    if (text) tip.append(chartNode("div", className, text));
  }
  tip.hidden = false;
  const box = row.getBoundingClientRect();
  const left = Math.min(box.left + window.scrollX, window.scrollX + document.documentElement.clientWidth
    - tip.offsetWidth - 8);
  tip.style.left = `${Math.max(window.scrollX + 8, left)}px`;
  tip.style.top = `${box.bottom + window.scrollY + 4}px`;
}

function hideChartTip() {
  chartTip().hidden = true;
}

// One chart: a group of bars per parameter, a bar per subject, one shared 0-100% axis.
function barChart(title, subtitle, groups, contenders, withInterval) {
  const figure = chartNode("figure", "chart");
  const caption = chartNode("figcaption");
  caption.append(chartNode("h3", "", title), chartNode("p", "chart-subtitle", subtitle));
  figure.append(caption);

  const body = chartNode("div", "chart-body");
  groups.forEach((group, groupIndex) => {
    if (groupIndex) body.append(chartNode("div", "chart-spacer"));
    const label = chartNode("div", "chart-label");
    label.style.gridRow = `span ${contenders.length}`;
    label.append(chartNode("span", "chart-name", group.label));
    if (group.hint) label.append(chartNode("span", "chart-hint", group.hint));
    body.append(label);
    const readings = contenders.map((contender, index) => chartReading(group.entries[index], withInterval));
    // Every subject unmeasured for the same reason: said once, not once per subject.
    if (readings.every((reading) => reading.missing && reading.missing === readings[0].missing)) {
      label.style.gridRow = "span 1";
      const track = chartNode("div", "chart-track");
      track.append(chartNode("span", "chart-missing", readings[0].missing));
      const lines = [readings[0].missing, ["", "every subject"],
        ["chart-tip-muted", (group.entries[0] || {}).definition || ""]];
      track.tabIndex = 0;
      track.addEventListener("pointerenter", () => showChartTip(track, lines));
      track.addEventListener("pointerleave", hideChartTip);
      track.addEventListener("focus", () => showChartTip(track, lines));
      track.addEventListener("blur", hideChartTip);
      track.setAttribute("aria-label", `${group.label}, every subject: ${readings[0].missing}`);
      body.append(track, chartNode("span", "chart-value", readings[0].structural ? "0%" : "–"));
      return;
    }
    contenders.forEach((contender, index) => {
      const reading = readings[index];
      const track = chartNode("div", "chart-track");
      if (reading.share !== undefined) {
        const bar = chartNode("div", "chart-bar");
        bar.style.width = `${reading.share * 100}%`;
        bar.style.background = `var(--series-${index + 1})`;
        track.append(bar);
        if (reading.interval) {
          const [low, high] = reading.interval;
          const whisker = chartNode("div", "chart-ci");
          whisker.style.left = `${low * 100}%`;
          whisker.style.width = `${(high - low) * 100}%`;
          track.append(whisker);
        }
      } else {
        track.append(chartNode("span", "chart-missing", reading.missing));
      }
      const value = chartNode("span", "chart-value", reading.value);
      const entry = group.entries[index] || {};
      const colour = `var(--series-${index + 1})`;
      const lines = [
        reading.share !== undefined ? reading.value : reading.missing,
        ["", contender.label],
        ["", reading.interval
          ? `95% interval ${chartPercent(reading.interval[0])}–${chartPercent(reading.interval[1])}` : ""],
        ["chart-tip-muted", entry.definition || ""],
      ];
      // The row is the hit target, not the painted bar: a 3% bar is a sliver
      // nobody lands on.
      for (const cell of [track, value]) {
        cell.addEventListener("pointerenter", () => showChartTip(track, lines, colour));
        cell.addEventListener("pointerleave", hideChartTip);
      }
      track.tabIndex = 0;
      track.addEventListener("focus", () => showChartTip(track, lines, colour));
      track.addEventListener("blur", hideChartTip);
      track.setAttribute("aria-label", `${group.label}, ${contender.label}: ${lines[0]}`);
      body.append(track, value);
    });
  });
  const axis = chartNode("div", "chart-axis");
  for (const tick of CHART_TICKS) {
    const mark = chartNode("span", "", chartPercent(tick));
    mark.style.left = `${tick * 100}%`;
    axis.append(mark);
  }
  body.append(axis);
  figure.append(body);
  figure.append(chartTable(groups, contenders, withInterval));
  return figure;
}

// Every number of the chart, readable without hovering and without colour.
function chartTable(groups, contenders, withInterval) {
  const details = chartNode("details", "chart-table");
  details.append(chartNode("summary", "", "Show as a table"));
  const table = chartNode("table");
  const head = table.createTHead().insertRow();
  head.append(chartNode("th", "", "Parameter"));
  for (const contender of contenders) head.append(chartNode("th", "", contender.label));
  const rows = table.createTBody();
  for (const group of groups) {
    const row = rows.insertRow();
    row.append(chartNode("th", "", group.label));
    contenders.forEach((contender, index) => {
      const reading = chartReading(group.entries[index], withInterval);
      let text = reading.share !== undefined ? reading.value
        : reading.structural ? `${reading.value} (none possible)` : reading.missing;
      if (reading.interval) {
        text += ` [${chartPercent(reading.interval[0])}–${chartPercent(reading.interval[1])}]`;
      }
      row.insertCell().textContent = text;
    });
  }
  details.append(table);
  return details;
}

function chartLegend(contenders) {
  const legend = chartNode("div", "chart-legend");
  contenders.forEach((contender, index) => {
    const item = chartNode("span", "chart-legend-item");
    const swatch = chartNode("span", "chart-swatch");
    swatch.style.background = `var(--series-${index + 1})`;
    item.append(swatch, document.createTextNode(contender.label));
    legend.append(item);
  });
  return legend;
}

function drawCampaignCharts(holder, data) {
  const every = data.contenders || [];
  const contenders = every.slice(0, CHART_SLOTS);
  const parameters = (contender) => data.parameters[contender.key] || {};
  holder.replaceChildren();
  if (!contenders.length) {
    holder.append(chartNode("p", "note", "No subject produced a result: there is nothing to chart."));
    return;
  }
  if (every.length > contenders.length) {
    holder.append(chartNode("p", "note",
      `${every.length - contenders.length} more subjects are in the campaign's report, not charted here.`));
  }
  holder.append(chartLegend(contenders));
  holder.append(barChart(
    "Evaluation parameters",
    "Share of the episodes, or of the tool calls, each rate counts; the whisker is its 95% interval. "
    + "The pass rate counts the repair oracle alone: a cell's PASS also needs the network converged "
    + "and the agent concluding and verifying its fix.",
    CHART_RATES.map(([key, label, hint, detail]) => ({
      label, hint: detail ? `${detail} · ${hint}` : hint,
      entries: contenders.map((contender) => parameters(contender)[key]),
    })),
    contenders, true));
  holder.append(barChart(
    "Ratio of type of action",
    "Each subject's tool calls by kind of action; a subject's bars add up to 100%.",
    CHART_ACTIONS.map(([key, label, hint]) => ({
      label,
      hint,
      entries: contenders.map((contender) => {
        const ratio = parameters(contender).ani_call_type_ratio || {};
        return key === "check_config" ? checkConfigEntry(ratio) : ratio[key];
      }),
    })),
    contenders, false));
}

// Called with the campaign's summary once it has ended; draws once.
async function renderCampaignCharts(run) {
  const section = document.getElementById("results");
  const holder = document.getElementById("charts");
  if (!section || section.dataset.drawn) return;
  section.dataset.drawn = "true";
  section.hidden = false;
  if (!run.report_path) {
    holder.replaceChildren(chartNode("p", "note",
      "No campaign report was written, so there is nothing to chart. Build it with "
      + `scripts/generate_campaign_report.py ${run.result_dir}, then reload this page.`));
    return;
  }
  try {
    const response = await fetch(`/api/campaigns/${run.id}/parameters`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    drawCampaignCharts(holder, body);
  } catch (error) {
    holder.replaceChildren(chartNode("p", "note", `The charts could not be drawn: ${error.message}`));
  }
}
