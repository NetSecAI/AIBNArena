# The diagnosis judge: evaluation parameter 13, "LLM found the problem rate"

Hugo's parameter sheet defines the parameter as: *an intermediate step where the LLM
explains the issue, compared with the methods of the scenario*. Until 2026-09-24 the
benchmark stood in with a proxy (which device the agent went to). This document is
the real thing: what the subject states, what it is compared against, how the
comparison is scored, and where the verdict lands.

## 1. The subject states the fault

On this branch the subjects' prompts are Hugo's and unchanged: neither the LangChain
baseline nor the LangChain RAG subject asks for a `diagnosis`, so their episodes are
judged from the conclusion's `summary` (`hypothesis_source: summary`). What follows
describes the step a prompt takes when it carries the shared stanza.


Every repair prompt ends with the shared stanza (`sut/common/messages.py`,
`FINAL_STATUS_CONTRACT`), whose JSON now has a third field:

```json
{"status":"completed"|"failed", "summary":"short observable conclusion",
 "diagnosis":"the fault as you identified it: which device, which configuration object, and what was wrong with it"}
```

An episode that ends without the subject's own conclusion (budget, interaction cap,
provider error) is asked once more, without tools, for the same statement
(`DIAGNOSIS_REQUEST`, 60 s, after the clock, so the repair is measured under the budget
and the diagnosis beside it). A completion claim the gate refused still carries the
subject's statement and is not asked again. An episode that ended on a transcript the
provider or the context bound refused is not asked: the transcript cannot be sent again.

The report carries the statement and its origin under `sut_result.diagnosis`:

| field | meaning |
|---|---|
| `text` | the statement, or null |
| `source` | `final_response`, `rejected_final_response`, `termination_turn` |
| `requested` | whether the tool-less turn was asked |
| `error` | why there is no text, when there is none |
| `calls`, `seconds` | the tool-less turn's cost; its tokens are in `execution.token_usage` |

The LangChain baseline fills the field from its structured conclusion and asks no
termination turn (its graph has no seam for one); its block says so.

## 2. The injected fault, in words

The judge compares the statement against the fault the compiler injected, put into a
sentence from the selected method and its bindings (`benchmarks/core/diagnosis.py`,
`describe_fault`). One clause per operation, naming the device, the object, what is
wrong and the healthy value where the bindings carry one:

> On app1, interface eth1 is configured with 10.10.50.10/32: the prefix length is
> wrong, it should be 10.10.50.10/24.

The method reaches the judge through `ScenarioDefinition.method`, which the loader
fills from the compiled instance. The scenario files are untouched.

## 3. The comparison: ParaPLUIE under a task-specific prompt

ParaPLUIE (Lemesle et al., *Paraphrase Generation Evaluation Powered by an LLM: A
Semantic Metric, Not a Lexical One*, COLING 2025) asks a language model a Yes/No
question about two texts and, instead of generating the answer, reads its confidence
in each answer off the next-token distribution:

```
score(A, B) = log p("Yes" | prompt(A, B)) - log p("No" | prompt(A, B))
```

Positive is agreement, negative disagreement, zero the natural threshold the papers
find close to the calibrated one. *-PLUIE (Lemesle et al., 2026, arXiv:2602.15778)
keeps the score and rewrites the question and its few-shot examples for the task at
hand, and shows that this tracks human judgement better than generated LLM verdicts
at a fraction of the cost. `FAULT_PLUIE` is that rewrite for "do A and B identify the
same network fault, that is the same device and the same broken configuration", with
eight examples on a network that is not one of ours, two of them phrased as the repair
that was made, since that is how a subject's conclusion usually reads
(`PROMPT_ID = fault-pluie-v2`; the template's sha256 is in every block).

The two log-probabilities are read in one of two ways, chosen by configuration
(`reading`) and named in every record (`judge.reading`):

- `prompt_logprobs`, the default: a vLLM endpoint. The assistant turn is continued
  with each answer (`continue_final_message`) and the prompt's own log-probabilities
  are requested (`prompt_logprobs`); the answer's token is the last one of that
  prompt, found by the token count the same conversation has without the answer,
  with a trailing end-of-turn token dropped where the chat template appends one
  (the Mistral tokenizer does). This is the papers' computation.
- `top_logprobs`: the OpenAI API (gpt-4.1, gpt-4o; gpt-5.1 with 5 candidates). One
  token is generated with `logprobs` and `top_logprobs`, and Yes and No are read
  from the candidates the API lists for that first position (up to 20). When both
  are listed the numbers are the same as the exact reading; when one is not, its
  log-probability is bounded by the smallest listed candidate and by the mass left
  over, the score is a bound, and the record says so (`judge.bound: true`). The
  verdict is still right in that case, since the listed answer dominates.

Under `top_logprobs` the request asks for exactly one generated token, because some
gateways report only the last generated position and a second token (the end of
turn) would hide the answer's candidates. `extra_body` (a JSON object, also
`--diagnosis-judge-extra-body` / `IBN_DIAGNOSIS_JUDGE_EXTRA_BODY`) is merged into
every request for what a gateway needs beyond the OpenAI shape; on OpenRouter,
`{"provider": {"order": ["Novita"], "allow_fallbacks": false, "require_parameters":
true}, "reasoning": {"enabled": false}}` pins the provider that returns the
candidates and keeps a reasoning model from thinking before the answer token, which
the papers' reading does not do (their generative variant scored below the direct
one). An endpoint that cannot serve the chosen reading leaves the episode unjudged,
with the reason. Every judge call is appended as one JSON line to an audit log
(`audit_log`; `benchmarks/run.py` defaults it to `diagnosis-audit.jsonl` beside the
reports, `scripts/score_diagnoses.py` to the scored directory): the two sentences,
the score, and the raw candidates or answer tokens the endpoint returned.

The model Hugo asked for is the paper's Mistral class: `Ministral-3-8B-Instruct-2512`
in bf16, served by vLLM behind a local relay on port 18002. On it a scoring call costs about 50 ms.

## 4. Where the verdict lands

`benchmarks/run.py` scores the episode in the `score_diagnosis` phase, after the
metrics, and writes `metrics.diagnosis`:

| field | meaning |
|---|---|
| `applicable` | false on a no-fault episode: nothing to find |
| `reference`, `method`, `operation`, `target` | the injected fault in words and in the compiler's terms |
| `hypothesis`, `hypothesis_source` | what was judged and where it came from (`final_response`, `termination_turn`, `rejected_final_response`, or `summary` for a record from before the step, scored offline) |
| `found` | true or false when judged, false when the subject stated nothing, null when the question was not asked |
| `score`, `log_p_yes`, `log_p_no` | the ParaPLUIE score and its two terms |
| `judge` | model, base URL, prompt id and sha256, route (`prompt_logprobs`) |
| `reason` | why `found` is null: no fault, no method, no judge configured, judge error |

The judge is a run condition and is named like the model: `--diagnosis-judge-model`,
`--diagnosis-judge-api-base` and `--diagnosis-judge-reading` on the command line, or
`IBN_DIAGNOSIS_JUDGE_MODEL`, `IBN_DIAGNOSIS_JUDGE_API_BASE`, `IBN_DIAGNOSIS_JUDGE_READING`
and `IBN_DIAGNOSIS_JUDGE_API_KEY` in the environment, or a `[diagnosis_judge]` table
(`model`, `api_base`, `reading`, `audit_log`) in the experiment file. The endpoint and
the key default to the subject's own provider (`LLM_API_BASE`, `LLM_API_KEY`), so a
judge on the same provider as the generator needs no second secret; no key is ever put
on a command line. Without a judge, the episode records the statement and `reason: no
diagnosis judge configured`, never a zero. A judge that cannot be reached leaves the
repair verdict as it is and records the error.

The console verdict and the web interface's verdict table show `root_cause_identified`,
`diagnosis_score`, `sut_diagnosis` and `diagnosis_judge` (the model that judged) under
the model rows; the HTML report has a "Diagnosis" section;
`scripts/evaluation_parameters.py` reports `llm_found_problem_rate` over the judged
episodes (an episode that stated nothing counts as not found), with the source
breakdown and the old device-based proxy kept beside it under `proxy`.

## 5. Scoring recorded episodes

```
python scripts/score_diagnoses.py ../runs/e8 --judge-model ministral-3-8b-instruct \
    --judge-api-base http://127.0.0.1:18002/v1 --write
```

replays each record's compiled instance (the same replay `evaluation_parameters.py`
uses for the faulty device), scores it, and with `--write` stores the block under
`metrics.diagnosis` marked `offline`. Records from before the step have no stated
diagnosis; their conclusion's `summary` is scored and the block says
`hypothesis_source: summary`, so a rate over mixed records can be split by source.

## 6. Reading the number

The score is one model's confidence under one prompt. Two scores are comparable only
under the same judge and prompt, which is why both are in the block. The rate says
whether the subject *named* the fault, not whether it fixed it: found-and-not-repaired
is the population this parameter exists to show, and a repaired episode whose
statement is judged wrong is worth reading by hand, since it is either a judge miss
or a subject that fixed what it did not understand.
