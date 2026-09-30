"""Every subject must advertise a distinct agent card.

The judge records provenance.sut_identity from the card's name. Three subjects
that share one name produce judged episodes that cannot be attributed to the
subject that ran them (seen 2026-09-04 when the tool and validated subjects were
created as twins of the archived scripted one).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SUBJECTS = (
    "sut/langchain_agent/a2a_server.py",
    "sut/langchain_rag_agent/a2a_server.py",
)


def _agent_card_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AgentCard":
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.append(str(keyword.value.value))
    return names


def test_every_subject_advertises_a_distinct_agent_card_name() -> None:
    seen: dict[str, str] = {}
    for relative in SUBJECTS:
        path = REPO / relative
        if not path.is_file():
            continue
        names = _agent_card_names(path)
        assert len(names) == 1, f"{relative}: expected one AgentCard, found {len(names)}"
        assert names[0] not in seen, (
            f"{relative} advertises {names[0]!r}, already used by {seen[names[0]]}")
        seen[names[0]] = relative
    assert seen, "no subject server found"
