"""Which system prompts each subject ships, for the form's prompt-variant field.

The directory is read rather than `sut.common.prompt_variants` imported: that
import pulls in a subject's whole runtime -- the A2A server, litellm -- and this
interface never runs a subject in its own process, it starts one. The two rules
that module states are the ones repeated here, and `tests/test_prompts.py` holds
this to the files on disk.
"""
from __future__ import annotations

from webui.config import REPOSITORY

from .architectures import Architecture

DEFAULT_VARIANT = "default"
#: Prompts a subject needs for another job. Reserved by `sut/common/prompt_variants.py`:
#: never a variant, because selecting one would measure something else entirely.
ROLE_NAMES = frozenset({"question"})


def variants(architecture: Architecture) -> tuple[str, ...]:
    """The prompts this subject can be run with, the shipped one first."""
    directory = REPOSITORY / architecture.module.rsplit(".", 1)[0].replace(".", "/") / "prompts"
    named = sorted(path.stem for path in directory.glob("*.txt")) if directory.is_dir() else []
    return (
        DEFAULT_VARIANT,
        *(name for name in named if name != DEFAULT_VARIANT and name not in ROLE_NAMES),
    )
