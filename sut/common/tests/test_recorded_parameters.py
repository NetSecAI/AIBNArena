"""Every run parameter a subject accepts must appear in what it records.

A condition that is set but not recorded cannot be read back from a result, which
makes the episode unattributable to the condition it ran under (seen 2026-09-05:
the archived subjects resolved a prompt variant and never named it in a result).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SUBJECTS = (
    "sut/langchain_agent/agent.py",
)

# What a run file can set on any subject, and therefore what a result must name.
REQUIRED = ("prompt_variant", "ani_call_limit",
            "enable_thinking", "thinking_style", "min_retry_tokens",
            "tool_result_chars", "context_budget_chars", "max_consecutive_rejections")


# What sut.common.thinking.thinking_record() contributes wherever it is spread in.
THINKING_RECORD_KEYS = frozenset({
    "enable_thinking", "thinking_style", "thinking_effort",
    "thinking_mechanism", "thinking_request", "thinking_directive_applied",
})


def _recorded_keys(path: Path) -> set[str]:
    """The parameters the subject names where it reports them.

    The dict is identified by `ani_call_limit`, which every subject writes as a
    literal key. A `**thinking_record(...)` spread in the same dict counts as
    naming what that helper contributes, since that is where those keys come from.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if "ani_call_limit" not in keys:
            continue
        for key, value in zip(node.keys, node.values):
            if key is None and isinstance(value, ast.Call) and (
                    getattr(value.func, "id", None) == "thinking_record"):
                keys |= THINKING_RECORD_KEYS
        return keys
    return set()


@pytest.mark.parametrize("subject", SUBJECTS)
def test_a_subject_records_every_parameter_a_run_can_set(subject: str) -> None:
    path = REPO / subject
    assert path.is_file(), subject
    keys = _recorded_keys(path)
    assert keys, f"{subject}: no parameter record found"
    missing = [name for name in REQUIRED if name not in keys]
    assert not missing, f"{subject} does not record {missing}"


@pytest.mark.parametrize("subject", SUBJECTS)
def test_a_subject_that_records_a_prompt_variant_also_resolves_one(subject: str) -> None:
    source = (REPO / subject).read_text(encoding="utf-8")
    # Recording the setting while ignoring it would be worse than not recording it.
    assert "resolve_system_prompt" in source, subject
