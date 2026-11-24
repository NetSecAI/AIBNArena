# Shared runtime for ANI v0.1 `self_execute` SUTs

Every SUT that the benchmark scores has to run the same loop: call a model, dispatch
its ANI tool calls, decide when the episode is over, and report what happened in the
shape the Judge parses. That machinery lives here once.

The reason is measurement, not tidiness. `device_changes[]` drives `action_execution_rate`,
the device count and the unsafe/failed action counts; the completion gate decides
whether `verified` can be true; the budget decides when an episode ends. Two SUTs
implementing those separately would drift, and the drift would show up in the results
as an agent-quality difference with nothing to say it was plumbing.

```text
Judge -> A2A -> SUT (prompts + model caller) -> sut/common (loop) -> ANI v0.1 -> ContainerLab
```

## What a SUT contributes

Two things: its prompts, and a model caller. Everything else is inherited.

Prompt text is not written in `agent.py`. Every prompt is a `.txt` file under the
SUT's own `prompts/` directory: `default.txt` is the prompt it ships, any other
stem is a wording a run can select with `prompt_variant`, and `question.txt` is
the sanity-check answerer's prompt — a role, loaded by name, never a variant.

```text
prompts/default.txt

You are ... whatever this agent is.

... its own strategy, its own policy ...

{FINAL_STATUS_CONTRACT}
Do not use Markdown.
```

```python
from sut.common import (
    DEFAULT_VARIANT, LiteLLMModelCaller, ModelAgentConfig, SelfExecuteRuntime,
    Tracer, execution_budget, invoke, load_prompt, resolve_system_prompt,
)

SUBJECT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT = load_prompt(SUBJECT_DIR, DEFAULT_VARIANT)
QUESTION_SYSTEM_PROMPT = load_prompt(SUBJECT_DIR, "question")

class MyConfig(ModelAgentConfig):
    @classmethod
    def from_env(cls):
        return cls(**cls.env_fields("MY_AGENT"))   # own prefix: no collision

class MyAgent:
    def __init__(self, config=None, ani=None):
        self.config = config or MyConfig.from_env()
        self.ani = ani or ContainerLabANI(ContainerLabEnv())

    def invoke(self, task_json: str) -> str:
        return invoke(self._runtime(), task_json)

    def _runtime(self) -> SelfExecuteRuntime:
        return SelfExecuteRuntime(
            ani=self.ani,
            call_model=self._call_model,      # resolved per call, so tests can swap it
            system_prompt=SYSTEM_PROMPT,
            question_system_prompt=QUESTION_SYSTEM_PROMPT,
            max_execution_seconds=self.config.max_execution_seconds,
            trace=Tracer(self.config.debug_trace),
            dry_run=self.config.dry_run,
            self_execute=self.config.self_execute,
        )

    def _call_model(self, messages, remaining_seconds):
        return LiteLLMModelCaller.from_config(self.config, tool_schemas=self.ani.tool_schemas)(
            messages, remaining_seconds
        )
```

[`sut/langchain_agent/`](../langchain_agent/) is the framework baseline and is
deliberately *not* a user of this loop -- it owns its own, through
`langchain.agents.create_agent`. That is the point of having it: it measures what the
shared loop costs relative to an off-the-shelf agent runtime over the same ANI.
[`sut/langchain_agent_v2/`](../langchain_agent_v2/) is that baseline without what coaches the model
or corrects it: no send-back, note or nudge, no method in its prompt. The step-back subjects of
[`sut/step-back/`](../step-back/) are that v2 with its reading taken in hand.

The server side is shared too: `SelfExecuteAgentExecutor` (serializes episodes,
keeps Uvicorn free to answer the agent card during a 300 s run), `build_ani` and
`resolve_endpoint`. A SUT's `a2a_server.py` is then argument parsing and a card.

## Modules

| Module | Holds |
|---|---|
| `self_execute.py` | The loop, the completion gate, the report, the sanity-question path |
| `messages.py` | Duck-typed model-message accessors, JSON parsing, the two prompt stanzas, and the nudge after a reply with no call |
| `repeated_requests.py` | The note on a failed request repeated identically, shared by this loop and the LangChain baseline |
| `ani_report.py` | ANI results → `device_changes[]`, `ani_operations[]`, the objective projection |
| `agent_config.py` | `ModelAgentConfig` defaults, `env_fields(prefix)`, `resolve_endpoint` |
| `litellm_backend.py` | The LiteLLM caller — the one replaceable piece |
| `trace.py` | `Tracer`: server-log-only audit trace, never returned over A2A |
| `a2a_app.py` | Executor, ANI wiring, log rendering |

## The prompt boundary

Prompt text belongs to each SUT: its role, its strategy, its policy. Two constants
are the exception, and they live in `messages.py` next to the parsers that enforce
them:

- `FINAL_STATUS_CONTRACT` — parsed by `parse_final_response`
- `ANSWER_CONTRACT` — parsed by `parse_answer`

An agent that declares a different output shape does not fail loudly. The parser
returns `None`, the loop never terminates on the model's own conclusion, and the
episode runs to `timeout` with no error naming the cause. That silence is why these
two are shared and asserted by the tests.

A prompt file asks for one by writing `{FINAL_STATUS_CONTRACT}` or
`{ANSWER_CONTRACT}`, which `load_prompt` substitutes on load. Substitution is by
name, not `str.format`, so any other brace in the prose — a JSON example, say —
needs no escaping.

## The loop

`run_self_execute` iterates while budget remains:

1. **Model call.** Any exception ends the episode with `status: "failed"`.
2. **Tool calls, if any.** Each is dispatched to the ANI; `ANIRequestError` becomes
   an `{"ok": false, ...}` tool result rather than a fatal error, so the model can
   react to its own bad argument. A passing `public_success_criteria` validation
   marks the most recent action `verified_after_action`. A `submit_result` the ANI
   accepts (v0.4) ends the episode there: the calls after it in the turn are not
   sent, its status is recorded as submitted, and `termination.via` names it. It is
   not gated: a `completed` without a passing validation stays `completed`, with
   `verified: false`.
3. **Final JSON, if no tool calls.** `completed` claimed without a passing
   validation is **rejected**: the loop pushes back a corrective message and keeps
   going. That gate is what stops a SUT from grading itself through its text.
4. **Neither.** The model is nudged and the loop continues. A reply whose text holds a tool
   call written out (`<tool_call>` markup the provider's parser did not read) is told so
   first, and its turn's error says so (`no_action_nudge`, since 2026-10-30: in E20 one
   `wrong_routing_table.m3` episode spent 343 turns writing calls as text, each answered
   with the bare nudge). The LangChain baseline's nudge uses the same function.
   A runtime with `end_on_no_tool_call` ends the episode there instead, with no nudge, and on a
   malformed call too; a `completed` written as text with no passing validation is recorded as
   failed, as `langchain_agent_v2` records it.

The default `status` is `"timeout"`, so anything that neither concludes nor fails
lands there. `verified` requires both `status == "completed"` and a passing
objective.

The report has eight keys — `mode`, `status`, `verified`, `device_changes`,
`ani_operations`, `execution`, `final_observation`, `error` — and only `mode` and
`device_changes` are validated Judge-side by `parse_self_execute_report`. Correctness is
never taken from the report: the Judge re-observes the lab itself.

The transcript is sent whole at every call unless a bound is set (`ModelAgentConfig`, all
off by default; `execution.context` records which ran and what each did):
`tool_result_chars` cuts one tool result; `context_budget_chars` elides the results of
reads a later identical read superseded and ends the episode when that is not enough;
`keep_recent_reads` keeps the most recent read results whole and turns every older one
into a stub naming its call, before every model call, without ever ending the episode
(`_elide_older_reads`); `keep_recent_read_chars` does the same counted in characters, the
answers to the model's last turn always kept whole, and with both a result stays whole only if
each rule keeps it. Writes, validations and the model's own messages are never elided.
The v2 subjects (`langchain_agent_v2` and the step-back subjects built on it) ship
`keep_recent_reads` at 10; a subject whose runtime
does not pass them runs without them. From 2026-11-15 to 2026-11-16 (main04) the v2 subjects
shipped `keep_recent_read_chars` at `KEEP_RECENT_READ_CHARS`, 60 000, and no count instead, so that
the rule did not depend on how large one subject's reads are; they were set back to ten after
main04. `IBN_SUT_KEEP_RECENT_READS` and `IBN_SUT_KEEP_RECENT_READ_CHARS` set either rule for every
subject of a run, 0 turning it off. `langchain_agent_v2`, which has no runtime of this loop, applies the same function
from a middleware, and sends what this loop sends.

## Failure handling

Malformed provider tool calls stay inside the loop: calls with no function name or
invalid JSON arguments are rejected before ANI, the model receives retry feedback,
and the public response remains on the `self_execute` contract. Model-provider errors
in both repair and question modes are likewise returned as explicit failed results.
The completion gate keeps its error while retrying so a rejected completion remains
visible if the model exhausts its budget.

## Tests

```bash
python sut/common/tests/test_self_execute_loop.py
```

Fifteen goldens run against **every** SUT listed in `AGENTS`, with a fake ANI and a
scripted model: no lab, no provider, no server, a few seconds to run. Adding a SUT
means writing a two-line builder and appending it in `_available_agents()`; the
existing entries are guarded by an `ImportError` check so the suite still runs while
a SUT is being landed.

The suite covers the happy path, the completion gate (including that its error
survives to the final report), ANI errors, model exceptions, the sanity-question
path, dry-run and disabled-self-execute short circuits, budget clamping, and the
malformed-call recovery and provider failures above.

Two prompt checks have deliberately different strengths: the terminal stanzas are a
hard assertion, while prompt-for-prompt equality between SUTs is only reported. That
equality is a control convention, not a machine requirement.
