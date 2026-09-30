"""The system prompt is an experiment parameter, and a run says which one it used.

The subject's own prompt stays the default; alternatives are files it ships under
`prompts/`. A run that names one the subject does not have must stop rather than
quietly measure the default under the name that was asked for.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sut.common import (
    DEFAULT_VARIANT,
    available_variants,
    load_prompt,
    resolve_system_prompt,
)
from sut.common.agent_config import ModelAgentConfig

SHIPPED = "the prompt the subject ships"


def test_the_default_is_the_prompt_the_subject_ships(tmp_path: Path) -> None:
    assert resolve_system_prompt(tmp_path, DEFAULT_VARIANT, SHIPPED) == SHIPPED
    assert resolve_system_prompt(tmp_path, None, SHIPPED) == SHIPPED
    assert resolve_system_prompt(tmp_path, "  ", SHIPPED) == SHIPPED
    # No prompts directory is not a defect: a subject may ship only its own prompt.
    assert available_variants(tmp_path) == [DEFAULT_VARIANT]


def test_a_named_variant_is_read_from_the_subject(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "terse.txt").write_text("Repair the network.\n", encoding="utf-8")

    assert resolve_system_prompt(tmp_path, "terse", SHIPPED) == "Repair the network."
    assert available_variants(tmp_path) == [DEFAULT_VARIANT, "terse"]


def test_an_unknown_variant_is_refused_and_says_what_the_subject_ships(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "terse.txt").write_text("Repair the network.\n", encoding="utf-8")

    with pytest.raises(ValueError) as refusal:
        resolve_system_prompt(tmp_path, "netconf_arena", SHIPPED)
    message = str(refusal.value)
    assert "netconf_arena" in message and "terse" in message and DEFAULT_VARIANT in message


def test_an_empty_variant_file_is_refused(tmp_path: Path) -> None:
    """An empty file would leave the model with no instructions while the record
    claims a prompt was set."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "blank.txt").write_text("   \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        resolve_system_prompt(tmp_path, "blank", SHIPPED)


def test_the_variant_is_a_config_field_settable_from_the_environment(monkeypatch) -> None:
    assert ModelAgentConfig().prompt_variant == DEFAULT_VARIANT

    monkeypatch.setenv("TEST_SUT_PROMPT_VARIANT", "terse")
    assert ModelAgentConfig.env_fields("TEST_SUT")["prompt_variant"] == "terse"

    monkeypatch.delenv("TEST_SUT_PROMPT_VARIANT")
    monkeypatch.setenv("IBN_SUT_PROMPT_VARIANT", "shared")
    assert ModelAgentConfig.env_fields("TEST_SUT")["prompt_variant"] == "shared"


def test_the_shipped_prompt_can_be_a_file(tmp_path: Path) -> None:
    """A subject may keep every prompt in `prompts/`, the shipped one included."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.txt").write_text("Repair the network.\n", encoding="utf-8")

    # No module constant is needed, and the file wins over one that is passed.
    assert resolve_system_prompt(tmp_path, DEFAULT_VARIANT) == "Repair the network."
    assert resolve_system_prompt(tmp_path, None) == "Repair the network."
    assert resolve_system_prompt(tmp_path, "  ") == "Repair the network."
    assert resolve_system_prompt(tmp_path, DEFAULT_VARIANT, SHIPPED) == "Repair the network."
    # `default` names the shipped prompt either way, so it is listed once.
    assert available_variants(tmp_path) == [DEFAULT_VARIANT]


def test_a_subject_with_no_prompt_at_all_is_refused(tmp_path: Path) -> None:
    """Neither a file nor a constant is a broken subject, not an empty prompt."""
    with pytest.raises(ValueError, match="no default prompt"):
        resolve_system_prompt(tmp_path, DEFAULT_VARIANT)


def test_an_empty_shipped_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.txt").write_text("   \n", encoding="utf-8")

    with pytest.raises(ValueError, match="is empty"):
        resolve_system_prompt(tmp_path, DEFAULT_VARIANT, SHIPPED)


def test_a_file_can_ask_for_a_shared_stanza(tmp_path: Path) -> None:
    """The loop parses the terminal stanzas, so files interpolate rather than copy."""
    from sut.common.messages import ANSWER_CONTRACT, FINAL_STATUS_CONTRACT

    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.txt").write_text(
        "Repair it.\n\n{FINAL_STATUS_CONTRACT}\n", encoding="utf-8")
    (tmp_path / "prompts" / "question.txt").write_text(
        "Answer it. {ANSWER_CONTRACT}\n", encoding="utf-8")

    assert FINAL_STATUS_CONTRACT in resolve_system_prompt(tmp_path, DEFAULT_VARIANT)
    assert "{FINAL_STATUS_CONTRACT}" not in resolve_system_prompt(tmp_path, DEFAULT_VARIANT)
    assert ANSWER_CONTRACT in load_prompt(tmp_path, "question")


def test_a_brace_that_is_not_a_stanza_is_left_alone(tmp_path: Path) -> None:
    """Substitution is by name, so prose may contain braces without escaping them."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.txt").write_text(
        'Return {"status": "completed"} exactly.\n', encoding="utf-8")

    assert resolve_system_prompt(tmp_path, None) == 'Return {"status": "completed"} exactly.'


def test_a_role_prompt_is_not_a_variant(tmp_path: Path) -> None:
    """Selecting the question prompt for the repair loop would measure something else."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.txt").write_text("Repair it.\n", encoding="utf-8")
    (tmp_path / "prompts" / "question.txt").write_text("Answer it.\n", encoding="utf-8")

    assert available_variants(tmp_path) == [DEFAULT_VARIANT]
    with pytest.raises(ValueError, match="role prompt"):
        resolve_system_prompt(tmp_path, "question")


def test_a_missing_role_prompt_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no prompt called"):
        load_prompt(tmp_path, "question")


def test_no_prompt_file_briefs_the_agent_on_the_environment() -> None:
    """Every prompt file, not just the ones a module constant reaches.

    `test_self_execute_loop` runs this check over the shared-loop SUTs' two module
    constants. Now that prompts are files, a variant nobody imports — and a subject
    outside that list, like the LangChain baseline — would go unchecked. Naming the
    platform, the nodes or the addressing hands the subject what the ANI is supposed
    to reveal, and the evaluation would measure a briefing rather than an agent.
    """
    import re

    forbidden = {
        "vendor or platform": r"SRLinux|SR Linux|sr_cli|nokia|containerlab|clab-|vyos|arista|cEOS",
        "node names": r"\bleaf\d|\bspine\d|\bweb1\b|\buser1\b|\bapp1\b|\bfinance1\b|\bguest1\b|external1|admin1",
        "addressing": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "interface names": r"ethernet-\d|\beth\d\b|mgmt0",
        "labs or scenarios": r"sme01|clos01|dmz_vlan|disable_interface",
    }
    files = sorted(Path("sut").glob("**/prompts/*.txt"))
    assert files, "no prompt files found; this test would pass vacuously"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden.items():
            hits = sorted({match.group(0) for match in re.finditer(pattern, text, re.IGNORECASE)})
            assert not hits, f"{path} leaks {label}: {hits}"
