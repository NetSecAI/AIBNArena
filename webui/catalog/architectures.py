"""The subjects this interface can start, and the flags each one accepts.

The flag sets are declared rather than read from the servers, because reading
them means importing the agent packages to draw a form. `tests/test_architectures.py`
holds them to their server's real argparse, so a flag added or dropped there
fails a test instead of reaching a subject that refuses it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


#: Flags every subject's server accepts, so they never need repeating below.
SHARED_FLAGS = frozenset({
    "model", "api_base", "api_key", "temperature", "max_tokens", "dry_run",
    "max_execution_seconds", "prompt_variant", "enable_thinking", "debug_trace",
    "ani_call_limit", "min_retry_tokens", "tool_result_chars",
    "context_budget_chars", "artifact_directory", "max_consecutive_rejections",
    "recursion_limit",
})

@dataclass(frozen=True)
class Architecture:
    key: str
    label: str
    module: str
    #: What the subject reports as `sut_identity` in its runtime document.
    identity: str
    #: The prefix its `from_env()` reads. Credentials are passed under it rather
    #: than under the repository-wide `LLM_*`, which it only falls back to: a
    #: `.env` naming this subject would otherwise win over the chosen endpoint.
    env_prefix: str
    default_port: int
    default_model: str
    flags: frozenset[str]
    prompt_variants: tuple[str, ...] = field(default_factory=tuple)

    def accepts(self, flag: str) -> bool:
        return flag in self.flags


ARCHITECTURES: tuple[Architecture, ...] = (
    Architecture(
        key="langchain_agent",
        label="LangChain baseline",
        module="sut.langchain_agent.a2a_server",
        identity="langchain_agent",
        env_prefix="LANGCHAIN_AGENT",
        default_port=8003,
        default_model="openai/gpt-4o-mini",
        flags=SHARED_FLAGS | {"max_context_chars"},
    ),
    # The baseline plus retrieval. Its --rag-* settings are not offered here: it
    # runs on its own rag_document/ corpus, or on LANGCHAIN_RAG_AGENT_RAG_* set in
    # the environment this interface is started from.
    Architecture(
        key="langchain_rag_agent",
        label="LangChain RAG",
        module="sut.langchain_rag_agent.a2a_server",
        identity="langchain_rag_agent",
        env_prefix="LANGCHAIN_RAG_AGENT",
        default_port=8004,
        default_model="openai/gpt-4o-mini",
        flags=SHARED_FLAGS | {"max_context_chars"},
    ),
)

BY_KEY = {architecture.key: architecture for architecture in ARCHITECTURES}


def architecture(key: str) -> Architecture:
    try:
        return BY_KEY[key]
    except KeyError:
        raise ValueError(f"unknown subject: {key}") from None
