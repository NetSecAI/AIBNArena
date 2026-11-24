"""Identical failed requests, counted the same way for every subject that uses it.

A request that failed, sent again unchanged while nothing it asks about has changed,
fails the same way. Until 2026-10-30 the LangChain subject ended its episode on the third
such request and the other subjects had no rule at all: in E20 six LangChain episodes
ended there, between 509 s and 1 129 s of 3 600, while the others ran on to their budget.
The rule is now the same everywhere, and it ends nothing: from the third identical
failure on, the answer carries a note saying so, and the episode goes on.

What "nothing has changed" means is the subject's to say, through `changed`: the
network, and anything else the subject reads and edits.
"""
from __future__ import annotations

import json
from typing import Any

#: The identical failures after which the answer carries the note.
REPEATED_REQUEST_LIMIT = 3

#: The key the note travels under in the answer the model reads.
REPEATED_REQUEST_KEY = "repeated_request"


def repeated_request_note(count: int) -> str:
    return (f"This exact request has now failed {count} times with the same answer, and "
            "nothing applied in between changed what it asks about. Sent again, it will "
            "fail the same way: change the request, or test a different hypothesis.")


class RepeatedRequests:
    """Counts each failed request by its operation, arguments and error.

    A failed `execute_validation` has no error, only its checks, so three failed
    validations of the same criteria count as one request whatever failed each time.
    `index` is the caller's position for the request, kept in the record as given.
    """

    def __init__(self, limit: int = REPEATED_REQUEST_LIMIT) -> None:
        self.limit = limit
        # Signature -> (operation, the indexes of its failures since the last change).
        self._counted: dict[str, tuple[str, list[int]]] = {}
        #: Each change that started the count again: its index, and what it cleared.
        self.resets: list[dict[str, Any]] = []
        #: Each answer that carried the note: the operation and the failures counted.
        self.notes: list[dict[str, Any]] = []

    def changed(self, index: int) -> None:
        """Something the counted requests asked about changed: each is a new question."""
        if self._counted:
            self.resets.append({"write": index, "cleared": [
                {"operation": operation, "counted": list(counted)}
                for operation, counted in self._counted.values()]})
            self._counted.clear()

    def failed(self, operation: str, arguments: Any, error: Any, index: int) -> str | None:
        """Count one failure; the note its answer carries, or None below the limit."""
        signature = json.dumps({"operation": operation, "arguments": arguments, "error": error},
                               sort_keys=True, default=str)
        _, counted = self._counted.setdefault(signature, (operation, []))
        counted.append(index)
        if len(counted) < self.limit:
            return None
        self.notes.append({"operation": operation, "counted": list(counted)})
        return repeated_request_note(len(counted))

    def record(self) -> dict[str, Any]:
        """What a report keeps of the rule."""
        return {"limit": self.limit, "action": "note", "notes": list(self.notes),
                "resets": list(self.resets)}
