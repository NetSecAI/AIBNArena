"""One scoring call to the diagnosis judge before anything is deployed (the forms' "Check the judge").

The judge runs last, after the episode: a judge its endpoint refuses -- a model id it does not
know, a key it rejects, a reading it cannot serve -- is only found once the episode is over, and
the record then holds the diagnosis unjudged. On 2026-11-17 three manual episodes went that way:
OpenAI refused `openai/gpt-4o-mini` as an invalid model ID. This makes the call the judge will
make, with the settings `benchmarks/run.py` gives it (`diagnosis_judge_override`): the model as
the endpoint receives it, the endpoint's base URL, its key or `LLM_API_KEY`, the reading, and
`IBN_DIAGNOSIS_JUDGE_EXTRA_BODY`. The pair it scores names one fault twice, so a working judge
answers Yes.
"""
from __future__ import annotations

import json
import os

import httpx

from benchmarks.core.diagnosis import JudgeConfig, JudgeError, PLUIEJudge
from webui.endpoints import Endpoint, served_name

#: The fault the check states, and a diagnosis that names it.
REFERENCE = "The interface eth2 of router r1 was administratively disabled."
HYPOTHESIS = "r1's eth2 interface had been shut down by configuration."


class JudgeUnavailable(RuntimeError):
    """The judge did not score the check's pair; the message says what its endpoint answered."""


def check_judge(
    model: str,
    reading: str | None,
    endpoint: Endpoint,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Score the check's pair with this judge, or raise JudgeUnavailable. Returns the line logged."""
    served, how = served_name(model), reading or "prompt_logprobs"
    try:
        extra = json.loads(os.environ["IBN_DIAGNOSIS_JUDGE_EXTRA_BODY"]) if os.getenv("IBN_DIAGNOSIS_JUDGE_EXTRA_BODY") else None
    except ValueError as exc:
        raise JudgeUnavailable(f"IBN_DIAGNOSIS_JUDGE_EXTRA_BODY is not JSON: {exc}") from exc
    config = JudgeConfig(
        model=served, api_base=endpoint.api_base.rstrip("/"),
        api_key=endpoint.api_key or os.getenv("LLM_API_KEY") or None,
        timeout_seconds=60.0, reading=how, extra_body=extra if isinstance(extra, dict) else None)
    try:
        scored = PLUIEJudge(config, transport=transport).score(REFERENCE, HYPOTHESIS)
    except (JudgeError, httpx.HTTPError, KeyError, ValueError) as exc:
        raise JudgeUnavailable(
            f"the diagnosis judge {served!r} at {config.api_base} ({how}) did not score a test pair: "
            f"{type(exc).__name__}: {str(exc).strip()[:400]}") from exc
    return (f"diagnosis judge {served!r} at {config.api_base} ({how}) scores: "
            f"{scored['score']:+.2f} on a pair that names one fault twice")
