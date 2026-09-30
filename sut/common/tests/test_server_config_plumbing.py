"""Every subject server must carry every field its config reads from the environment.

The launcher hands run conditions to a subject through IBN_SUT_* variables that
`ModelAgentConfig.env_fields` reads (through each subject's from_env). Each a2a_server then builds its final config from the
command line, field by field, falling back to env_config; a field left out of that
construction is silently lost. That is how trace_ref came back null in every record
of the first D1 window: the servers dropped env_config.trace_directory. This scan
pins the set: a field from_env reads must be named in the server's construction.
"""
from __future__ import annotations

import dataclasses
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AGENT_CONFIG = ROOT / "sut" / "common" / "agent_config.py"
SERVERS = sorted(
    path for path in ROOT.glob("sut/**/a2a_server.py")
    if "env_config" in path.read_text(encoding="utf-8")
)


def _fields_read_from_env() -> set[str]:
    source = AGENT_CONFIG.read_text(encoding="utf-8")
    start = source.index("def env_fields(")
    end = source.index("\ndef ", start)
    body = source[start:end]
    # The dict literal env_fields returns: every `"name": (` or `"name": optional_int(` key.
    return set(re.findall(r'^\s+"([a-z_]+)":\s', body, flags=re.M))


def _construction_block(server: Path) -> str:
    source = server.read_text(encoding="utf-8")
    match = re.search(r"config = \w+Config\((.*?)\n\s*\)\n", source, flags=re.S)
    assert match, f"{server}: no Config(...) construction found"
    return match.group(1)


#: Fields a server may deliberately not carry: the two endpoint fields are resolved
#: separately by resolve_endpoint, and the identity fields are not conditions.
EXEMPT = {"api_base", "api_key", "model"}


def test_env_fields_reads_the_fields_this_scan_expects():
    fields = _fields_read_from_env()
    assert {"trace_directory", "artifact_directory", "tool_result_chars",
            "context_budget_chars", "max_consecutive_rejections"} <= fields, fields


@pytest.mark.parametrize("server", SERVERS, ids=[str(p.relative_to(ROOT)) for p in SERVERS])
def test_server_carries_every_env_field(server: Path):
    block = _construction_block(server)
    carried = set(re.findall(r"^\s+([a-z_]+)=", block, flags=re.M))
    missing = sorted(f for f in _fields_read_from_env() - EXEMPT if f not in carried)
    assert not missing, f"{server.relative_to(ROOT)} drops env_config fields: {missing}"


def _config_class(server: Path) -> type:
    """The config class the server constructs, taken from the server's own namespace."""
    match = re.search(r"config = (\w+Config)\(", server.read_text(encoding="utf-8"))
    assert match, f"{server}: no Config(...) construction found"
    module = importlib.import_module(".".join(server.relative_to(ROOT).with_suffix("").parts))
    return getattr(module, match.group(1))


@pytest.mark.parametrize("server", SERVERS, ids=[str(p.relative_to(ROOT)) for p in SERVERS])
def test_server_carries_every_field_of_its_config(server: Path):
    """A subject's own settings are lost the same way, and env_fields does not list them.

    LangChainAgentConfig.from_env reads LANGCHAIN_AGENT_TOOL_CHOICE, and the
    baseline's server left tool_choice out of its construction: the setting was read
    and discarded, and every run bound "auto" whatever the environment said (fixed
    2026-09-24). The scan above could not see it, since it knows only the fields the
    shared env_fields reads. What a server must carry is every field of the config
    class it builds, the subject's own included.
    """
    block = _construction_block(server)
    carried = set(re.findall(r"^\s+([a-z_]+)=", block, flags=re.M))
    missing = sorted(field.name for field in dataclasses.fields(_config_class(server))
                     if field.name not in carried and field.name not in EXEMPT)
    assert not missing, f"{server.relative_to(ROOT)} drops config fields: {missing}"
