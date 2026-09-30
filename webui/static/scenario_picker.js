// The scenario a form runs, chosen the same way on the Episode page and in each
// row of a campaign: the scenario first, then the wording of its task, its
// method, the topology it runs on and the reference state it starts from there.
// A topology and a state together name one experiment file, which is what the
// judge and the subject are given. The false-positive files are not offered:
// each is another file's instance with its fault never injected, which the No
// fault box asks of any of them. Every choice comes from window.CATALOG.

function scenarioKey(scenario) {
  return `${scenario.domain}.${scenario.name}`;
}

function isFalsePositiveFile(key) {
  return window.CATALOG.experiments.some((item) => item.key === key && !item.fault_applicable);
}

// Every scenario an offered experiment file can run, once each, in file order.
function scenarioChoices() {
  const found = new Map();
  for (const item of window.CATALOG.experiments.filter((preset) => preset.fault_applicable)) {
    for (const scenario of item.scenarios) {
      if (!found.has(scenarioKey(scenario))) found.set(scenarioKey(scenario), scenario);
    }
  }
  return [...found.values()];
}

// The files that can run this scenario, and whether one was written for it:
// its own scenario_id is the scenario's.
function runners(key) {
  return window.CATALOG.experiments.filter((item) => item.fault_applicable
    && item.scenarios.some((scenario) => scenarioKey(scenario) === key));
}

function writtenFor(item, key) {
  return item.default_scenario_id.startsWith(`${key}.`);
}

// Where and from what state this scenario can run: the files written for it,
// or every file that runs it when none is. A file pins one topology and one
// reference state, so this bounds both lists -- qos.assured_bandwidth is
// written for sme01-qos-greenfield alone, qos.wan_shaping_policy_repair for
// sme01-qos alone, as scripts/export_results.py records it too.
function possibleFiles(key) {
  const pool = runners(key);
  const written = pool.filter((item) => writtenFor(item, key));
  return written.length ? written : pool;
}

// A server started before the catalog carried the state names hands none; each
// file then stands for its own state rather than all of them for one.
function stateOf(item) {
  return item.reference_state || item.key;
}

// A scenario puts its fault to the subject in several wordings, from a precise
// intent to a vague one (the file's low, medium and high): the less the intent
// says, the harder the task.
const DIFFICULTY = { high: "easy", medium: "medium", low: "hard" };
const BY_DIFFICULTY = ["high", "medium", "low"];

function wordingOf(task) {
  return task.variant ?? "";
}

function taskLabel(task) {
  if (task.variant === null) return "the scenario's only wording";
  const difficulty = DIFFICULTY[task.variant];
  return (difficulty ? `${difficulty} — ${task.variant} precision` : task.variant)
    + (task.base ? " (default)" : "");
}

// A server started before the catalog carried the wordings hands none.
function sortedTasks(scenario) {
  const rank = (task) => {
    const place = BY_DIFFICULTY.indexOf(task.variant);
    return place === -1 ? BY_DIFFICULTY.length : place;
  };
  return [...(scenario.tasks || [])].sort((a, b) => rank(a) - rank(b));
}

// The wording itself, its placeholders named as the compiler will fill them.
function wordingText(task) {
  return `“${task.wording.replace(/\{\{\s*(\w+)\s*\}\}/g,
    (_, name) => `‹${name.replace(/_/g, " ")}›`)}”`;
}

// The instance the judge is given: the method's id, with the wording's name in
// it unless the wording is the scenario's base, which keeps the bare id.
function instanceId(methodId, task) {
  if (!task || task.base || task.variant === null) return methodId;
  const cut = methodId.lastIndexOf(".");
  return `${methodId.slice(0, cut)}.${task.variant}${methodId.slice(cut)}`;
}

// "connectivity.disable_interface.low.m1" -> its scenario, its wording (null
// for the base wording's bare id) and its method's bare id.
function splitScenarioId(id) {
  const parts = id.split(".");
  const key = parts.slice(0, 2).join(".");
  return { key, variant: parts.length > 3 ? parts[2] : null, method: `${key}.${parts[parts.length - 1]}` };
}

function fillSelect(select, options) {
  select.innerHTML = "";
  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.value;
    element.textContent = option.label;
    select.append(element);
  }
}

// One set of fields -- scenario, task, method, topology, state (whose value is
// the experiment file) and the No fault box -- kept to the rules above. Notes
// go to `taskNote` and `stateNote` when the form has room for them, and to the
// fields' own titles when it does not, as in a campaign row.
//
// A recorded selection -- a seed from the register, a row of a recorded
// campaign -- replays on the file and the instance it was written down as,
// whatever the lists would offer, so what it needs is offered for it, marked,
// and dropped by `release()`.
//
// Hooks: `onFileChange(file)` when another experiment file is chosen, and
// `onEdit()` when the person changes any field, after the fields follow.
class ScenarioPicker {
  constructor(fields, hooks = {}) {
    this.fields = fields;
    this.hooks = hooks;
    this.chosenWording = null;  // a wording chosen by hand, kept across scenarios
    this.replaying = false;     // the lists hold a recorded selection's file
    const { scenario, task, method, topology, state } = fields;
    fillSelect(scenario, scenarioChoices().map((item) => ({
      value: scenarioKey(item), label: scenarioKey(item),
    })));
    scenario.addEventListener("change", () => { this.onScenario(); this.edited(); });
    task.addEventListener("change", () => {
      this.chosenWording = task.value;
      this.describeTask();
      this.edited();
    });
    method.addEventListener("change", () => this.edited());
    topology.addEventListener("change", () => { this.fillStates(); this.edited(); });
    state.addEventListener("change", () => { this.onFile(); this.edited(); });
    this.onScenario();
  }

  edited() {
    if (this.hooks.onEdit) this.hooks.onEdit();
  }

  scenario() {
    return scenarioChoices().find((item) => scenarioKey(item) === this.fields.scenario.value);
  }

  file() {
    return window.CATALOG.experiments.find((item) => item.key === this.fields.state.value);
  }

  task() {
    const tasks = this.scenario().tasks || [];
    return tasks.find((task) => wordingOf(task) === this.fields.task.value)
      || tasks.find((task) => task.base) || null;
  }

  scenarioId() {
    return instanceId(this.fields.method.value, this.task());
  }

  // What the server is sent for this selection.
  selection() {
    return {
      experiment: this.fields.state.value,
      scenario_id: this.scenarioId(),
      no_fault: this.fields.noFault.checked,
    };
  }

  onScenario(recorded) {
    const scenario = this.scenario();
    fillSelect(this.fields.method, scenario.methods.map((method) => ({
      value: method.scenario_id,
      label: `m${method.id} — ${method.name}`,
    })));
    this.fillTasks();
    this.fillTopologies(recorded);
  }

  // The scenario's wordings, easiest first. The base one is chosen unless one
  // was chosen by hand and this scenario has it too. With none from the server,
  // the field hides and the base wording's bare id is sent.
  fillTasks() {
    const tasks = sortedTasks(this.scenario());
    (this.fields.taskField || this.fields.task).hidden = !tasks.length;
    fillSelect(this.fields.task, tasks.map((task) => ({
      value: wordingOf(task), label: taskLabel(task),
    })));
    const chosen = tasks.find((task) =>
      this.chosenWording !== null && wordingOf(task) === this.chosenWording)
      || tasks.find((task) => task.base) || tasks[0];
    if (chosen) this.fields.task.value = wordingOf(chosen);
    this.describeTask();
  }

  describeTask() {
    const task = this.task();
    const text = task ? wordingText(task) : "";
    if (this.fields.taskNote) this.fields.taskNote.textContent = text;
    else this.fields.task.title = text;
  }

  // The topologies this scenario can run on. The topology chosen stays chosen
  // while it is offered.
  fillTopologies(recorded) {
    const { topology } = this.fields;
    const key = this.fields.scenario.value;
    const wanted = window.CATALOG.experiments.find((item) => item.key === recorded) || null;
    const topologies = [...new Set(possibleFiles(key).map((item) => item.topology))];
    const extra = Boolean(wanted) && !topologies.includes(wanted.topology);
    if (extra) topologies.push(wanted.topology);
    const current = topology.value;
    fillSelect(topology, topologies.map((name) => ({
      value: name,
      label: name + (extra && name === wanted.topology ? " (recorded seed)" : ""),
    })));
    topology.value = wanted ? wanted.topology
      : topologies.includes(current) ? current : topologies[0];
    this.replaying = Boolean(wanted);
    this.fillStates(recorded);
  }

  // The reference states this scenario can start from on this topology, one per
  // file: on sme_wan_edge_qos, healthy_greenfield for qos.assured_bandwidth and
  // healthy for qos.wan_shaping_policy_repair, never the other's. A recorded
  // selection on another state is replayed on it, and the note says it is not
  // the scenario's own.
  fillStates(recorded) {
    const { state } = this.fields;
    const key = this.fields.scenario.value;
    const before = state.value;
    const current = this.file();
    const byState = new Map();
    for (const item of possibleFiles(key).filter((file) =>
      file.topology === this.fields.topology.value)) {
      if (!byState.has(stateOf(item))) byState.set(stateOf(item), item);
    }
    const wanted = window.CATALOG.experiments.find((item) => item.key === recorded) || null;
    const replaced = wanted && byState.get(stateOf(wanted)) !== wanted ? wanted : null;
    if (wanted) byState.set(stateOf(wanted), wanted);
    const offered = [...byState.values()];
    fillSelect(state, offered.map((item) => ({
      value: item.key,
      label: stateOf(item) + (item === replaced ? " (recorded seed)" : ""),
    })));
    const chosen = wanted
      || offered.find((item) => writtenFor(item, key))
      || offered.find((item) => current && stateOf(item) === stateOf(current))
      || offered[0];
    if (chosen) state.value = chosen.key;
    // Only a different file resets what follows from it, such as a typed budget.
    if (state.value !== before) this.onFile();
    else this.describeFile();
  }

  describeFile() {
    const chosen = this.file();
    if (!chosen) return;
    const key = this.fields.scenario.value;
    const own = runners(key).find((item) => writtenFor(item, key));
    const text =
      `experiment ${chosen.key} · testbed ${chosen.testbed_id} · cleanup ${chosen.cleanup}`
      + (chosen.fault_applicable ? "" : " · a false-positive file: its fault is never injected")
      + (own && stateOf(own) !== stateOf(chosen) ? ` · ${key} is written for ${stateOf(own)}` : "");
    if (this.fields.stateNote) this.fields.stateNote.textContent = text;
    else this.fields.state.title = text;
  }

  onFile() {
    const chosen = this.file();
    if (!chosen) return;
    this.describeFile();
    // A false-positive file never injects, whatever the box says, so the box says
    // so and cannot be unticked. Leaving one hands the box back unticked.
    const box = this.fields.noFault;
    if (!chosen.fault_applicable) box.checked = true;
    else if (box.disabled) box.checked = false;
    box.disabled = !chosen.fault_applicable;
    if (this.hooks.onFileChange) this.hooks.onFileChange(chosen);
  }

  // Put back what a recorded selection ran: its scenario, wording, method, file
  // and No fault box. The file is offered for it whatever the lists would.
  restore(entry) {
    const recorded = splitScenarioId(entry.scenario_id);
    this.fields.scenario.value = recorded.key;
    this.onScenario(entry.experiment);
    const base = (this.scenario().tasks || []).find((task) => task.base);
    this.fields.task.value = recorded.variant ?? (base ? wordingOf(base) : "");
    this.describeTask();
    this.fields.method.value = recorded.method;
    if (!this.fields.noFault.disabled) this.fields.noFault.checked = Boolean(entry.no_fault);
  }

  // What was offered for a recorded selection goes with it.
  release() {
    if (this.replaying) this.fillTopologies();
  }
}
