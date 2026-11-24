// The diagnosis judge (evaluation parameter 13) on the Episode and Campaign pages: none,
// a model served by vLLM, or one served by the OpenAI API.
//
// The two read the judge's Yes/No log-probabilities differently, so the kind decides the
// reading sent with the request: vLLM gives the prompt's own log-probabilities, the exact
// reading (`prompt_logprobs`); the OpenAI API only lists the candidates of a generated
// token (`top_logprobs`). The model list offers the registered models of the kind chosen.
// An endpoint is read as the OpenAI API when it was registered as OpenAI or OpenRouter, or
// when its URL is one of theirs: OpenRouter takes the OpenAI API's `logprobs` and
// `top_logprobs`, not vLLM's `prompt_logprobs`. Any other is taken for vLLM.

const JUDGE_OTHER = "\u0000judge-other";
const JUDGE_READINGS = { vllm: "prompt_logprobs", openai: "top_logprobs" };

function judgeKindOf(endpoint) {
  return ["openai", "openrouter"].includes(endpoint.provider)
    || /^https?:\/\/(api\.openai\.com|openrouter\.ai)(\/|$)/.test(endpoint.api_base)
    ? "openai" : "vllm";
}

function judgeModels(kind) {
  return window.CATALOG.endpoints.filter((endpoint) => judgeKindOf(endpoint) === kind)
    .flatMap((endpoint) => endpoint.models.map((model) => ({ model, endpoint })));
}

// Draw the model list for the kind chosen. The model chosen stays chosen while it is
// offered, and the free-text id while one is typed; otherwise the first model of that kind
// is chosen, or the free-text id when there is none.
function fillJudgeModels(otherLabel) {
  const kind = document.getElementById("judge-kind").value;
  const select = document.getElementById("diagnosis-judge");
  const previous = select.value;
  const offered = kind ? judgeModels(kind) : [];
  select.innerHTML = "";
  for (const item of offered) {
    const option = document.createElement("option");
    option.value = item.model;
    option.textContent = `${item.model} — ${item.endpoint.name}`;
    select.append(option);
  }
  const other = document.createElement("option");
  other.value = JUDGE_OTHER;
  other.textContent = otherLabel;
  select.append(other);
  const typed = previous === JUDGE_OTHER && document.getElementById("diagnosis_judge_model").value.trim();
  select.value = offered.some((item) => item.model === previous) || typed
    ? previous : (offered[0] ? offered[0].model : JUDGE_OTHER);
  showJudgeFields();
}

function showJudgeFields() {
  const kind = document.getElementById("judge-kind").value;
  document.getElementById("judge-model-field").hidden = !kind;
  document.getElementById("diagnosis-judge-custom").hidden =
    !kind || document.getElementById("diagnosis-judge").value !== JUDGE_OTHER;
  const note = document.getElementById("judge-note");
  if (note) {
    note.textContent = kind && !judgeModels(kind).length
      ? `No ${kind === "openai" ? "OpenAI or OpenRouter" : "vLLM"} endpoint is registered: add one on the Models page, or type a model id.`
      : "";
  }
}

function chosenJudge() {
  if (!document.getElementById("judge-kind").value) return "";
  const choice = document.getElementById("diagnosis-judge").value;
  return choice === JUDGE_OTHER ? document.getElementById("diagnosis_judge_model").value.trim() : choice;
}

// What the request carries for the judge: its model and the reading its kind takes.
function judgeRequest() {
  const model = chosenJudge();
  return model
    ? { diagnosis_judge_model: model, diagnosis_judge_reading: JUDGE_READINGS[document.getElementById("judge-kind").value] }
    : {};
}

// Put a recorded request's judge back: its kind from its reading, then its model.
function restoreJudge(model, reading, otherLabel) {
  document.getElementById("judge-kind").value = !model ? "" : reading === "top_logprobs" ? "openai" : "vllm";
  fillJudgeModels(otherLabel);
  if (!model) return;
  const offered = judgeModels(document.getElementById("judge-kind").value).some((item) => item.model === model);
  document.getElementById("diagnosis-judge").value = offered ? model : JUDGE_OTHER;
  if (!offered) document.getElementById("diagnosis_judge_model").value = model;
  showJudgeFields();
}
