"""What the form submits: one episode, fully stated.

Every field a person can set in the browser is here, and nothing is inferred
later from somewhere else. A condition that reaches the subject or the judge must
have been written down in this request first, so what a run was asked to do and
what its record says cannot drift apart.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


#: The output-token ceiling a subject is started with when the form leaves it empty.
DEFAULT_MAX_TOKENS = 2048


class EpisodeRequest(BaseModel):
    # What is being measured.
    architecture: str
    experiment: str
    scenario_id: str
    model: str = Field(min_length=1)
    seed: int = 0
    #: Draw a fresh seed and write down what it resolved to, instead of using
    #: `seed`. A seed that stands for one recorded context is what lets a later
    #: run be asked for by that number alone.
    new_seed: bool = False
    #: The campaign this episode is a cell of, when it is one. Recorded in the
    #: judge's provenance, the same in every record of one campaign.
    seed_campaign: int | None = None
    execution_budget_seconds: float = Field(gt=0)
    no_fault: bool = False
    cleanup: Literal["restore", "destroy"] | None = None
    #: The model that judges the subject's stated diagnosis against the injected
    #: fault (evaluation parameter 13, ParaPLUIE). A model a registered endpoint
    #: serves: its base URL goes to the judge on the command line, its key through
    #: the environment. None records the diagnosis unjudged.
    diagnosis_judge_model: str | None = None
    #: How that judge reads the Yes/No log-probabilities: `prompt_logprobs` (vLLM,
    #: exact) or `top_logprobs` (OpenAI API: the candidates of the generated token).
    diagnosis_judge_reading: Literal["prompt_logprobs", "top_logprobs"] | None = None
    #: Make one scoring call to the diagnosis judge before anything starts -- at Validate, and
    #: before the subject (`webui/judge_check.py`) -- and stop when it does not score. Without
    #: it, a judge its endpoint refuses is found after the episode, which is then unjudged.
    check_diagnosis_judge: bool = True

    # Where the subject is served.
    host: str = "127.0.0.1"
    port: int | None = None

    # Model and loop settings the subject's server accepts as flags.
    #: Sent to the subject as --max-tokens unless set otherwise. The subjects' own default,
    #: 400 (sut/common/agent_config.py), cut gpt-4o-mini's replies mid-text, which ended an
    #: episode with no tool call (reports/manual, 2026-11-17). A recorded request that
    #: stated null keeps it.
    max_tokens: int | None = DEFAULT_MAX_TOKENS
    temperature: float | None = None
    prompt_variant: str | None = None
    enable_thinking: bool | None = None
    debug_trace: Literal["off", "summary", "full"] | None = None
    ani_call_limit: int | None = None
    min_retry_tokens: int | None = None
    tool_result_chars: int | None = None
    context_budget_chars: int | None = None
    max_context_chars: int | None = None
    max_consecutive_rejections: int | None = None
    recursion_limit: int | None = None
    artifact_directory: str | None = None
    dry_run: bool = False

    # Read by the subject from its environment, never from a flag. The page does not set
    # thinking: it is chosen when the model is declared (Models page) and written here at
    # launch from that declaration (`endpoints.with_thinking`), so the record states it.
    thinking_style: Literal["auto", "chat_template", "system_prompt"] | None = None
    thinking_effort: Literal["minimal", "low", "medium", "high"] | None = None

    def sut_url(self, port: int) -> str:
        return f"http://{self.host}:{port}"


def slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "-", value).strip("-").lower()


def run_id() -> str:
    """A name for one run: sortable, and distinct from a run started the same second."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def experiment_id(request: EpisodeRequest) -> str:
    """What the judge records this episode as: the scenario instance and its seed.

    Also the folder the episode is written to, under the manual directory; each run
    of it gets its own subfolder there, named by the run id, so a seed run again does
    not overwrite the first.
    """
    return f"{slug(request.scenario_id)}-seed{request.seed}"
