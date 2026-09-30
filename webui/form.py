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

    # Where the subject is served.
    host: str = "127.0.0.1"
    port: int | None = None

    # Model and loop settings the subject's server accepts as flags.
    max_tokens: int | None = None
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

    # Read by the subject from its environment, never from a flag.
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


def experiment_id(request: EpisodeRequest, identifier: str) -> str:
    """What the judge records this episode as, and how its result file is found."""
    return f"webui-{slug(request.scenario_id)}-seed{request.seed}-{identifier}"
