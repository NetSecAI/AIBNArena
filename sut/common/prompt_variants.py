"""Selecting which system prompt a subject runs with.

The prompt is an experiment parameter: the same subject is measured under the
prompt it ships with and under alternatives, and a result has to say which one
was in force. Every prompt is a file under the subject's `prompts/` directory,
including the shipped one as `default.txt`, so adding or changing a wording is
touching a file rather than editing an agent. A subject that has no `default.txt`
keeps its shipped prompt as a reviewed module constant, passed in as
`default_prompt`.

Two kinds of file live there. A *variant* is a wording of the agent's own system
prompt, and its stem is the name a run selects with `prompt_variant`. A *role* is
a different prompt the subject needs for a different job — `question.txt`, the
sanity-check answerer — and its name is reserved: it is loaded by name with
`load_prompt`, never offered as a variant, because it is not a rewording of the
agent prompt and selecting it would silently measure the wrong thing.

The terminal stanzas stay owned by `messages.py`, because the loop parses them and
a subject that declares a different shape fails silently. A file asks for one by
writing `{FINAL_STATUS_CONTRACT}` or `{ANSWER_CONTRACT}`, substituted on load.
Substitution is by explicit replacement rather than `str.format`, so a literal
brace anywhere else in the prose is just a brace.

Refusals are by name and list what the subject actually ships: a run that asked
for a prompt the subject does not have must stop at startup, not quietly measure
the default and record the name that was asked for.
"""
from __future__ import annotations

from pathlib import Path

from .messages import ANSWER_CONTRACT, FINAL_STATUS_CONTRACT

DEFAULT_VARIANT = "default"
PROMPTS_DIRNAME = "prompts"
SUFFIX = ".txt"

#: Prompts a subject needs for a job other than the repair loop. Reserved: never a
#: variant, always loaded by name.
ROLE_NAMES = frozenset({"question"})

#: What a prompt file may ask `messages.py` for, by name.
PLACEHOLDERS = {
    "FINAL_STATUS_CONTRACT": FINAL_STATUS_CONTRACT,
    "ANSWER_CONTRACT": ANSWER_CONTRACT,
}


def prompt_path(subject_dir: Path, name: str) -> Path:
    """Where the prompt called `name` would live for this subject."""
    return Path(subject_dir) / PROMPTS_DIRNAME / f"{name}{SUFFIX}"


def load_prompt(subject_dir: Path, name: str) -> str:
    """The text of one prompt file, with the shared stanzas substituted in."""
    path = prompt_path(subject_dir, name)
    if not path.is_file():
        raise ValueError(f"this subject ships no prompt called {name!r}: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        # An empty file would leave the model with no instructions at all, which
        # reads in the record as a prompt that was set rather than one that is missing.
        raise ValueError(f"prompt {name!r} is empty: {path}")
    for key, value in PLACEHOLDERS.items():
        text = text.replace("{" + key + "}", value)
    return text


def available_variants(subject_dir: Path) -> list[str]:
    """Every prompt this subject can run with, the shipped one first."""
    directory = Path(subject_dir) / PROMPTS_DIRNAME
    named = sorted(path.stem for path in directory.glob(f"*{SUFFIX}")) if directory.is_dir() else []
    # `default` names the shipped prompt whether it is a file here or a module
    # constant, so it is listed once either way; a role is not a variant.
    return [
        DEFAULT_VARIANT,
        *(name for name in named if name != DEFAULT_VARIANT and name not in ROLE_NAMES),
    ]


def resolve_system_prompt(
    subject_dir: Path, variant: str | None, default_prompt: str | None = None
) -> str:
    """The prompt text for `variant`, or the subject's own when none is asked for."""
    name = (variant or DEFAULT_VARIANT).strip()
    # A blank setting is "not set", which is the subject's own prompt, not a name.
    if not name:
        name = DEFAULT_VARIANT
    if name in ROLE_NAMES:
        # It exists as a file, which is why this is worth saying plainly: running the
        # repair loop under the question prompt would measure something else entirely.
        raise ValueError(
            f"{name!r} is a role prompt, not a variant of the agent prompt; "
            f"this subject ships {available_variants(subject_dir)}"
        )
    if prompt_path(subject_dir, name).is_file():
        return load_prompt(subject_dir, name)
    if name == DEFAULT_VARIANT:
        if default_prompt is None:
            # Neither a file nor a constant: the subject has no prompt at all, which
            # must stop the run rather than send the model an empty instruction.
            raise ValueError(
                f"subject ships no default prompt: no {prompt_path(subject_dir, name)} "
                "and no module constant"
            )
        return default_prompt
    raise ValueError(
        f"unknown prompt variant {name!r}; this subject ships "
        f"{available_variants(subject_dir)}"
    )
