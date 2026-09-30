"""Model-message plumbing, framework-neutral.

Every accessor is duck-typed so the same code reads an OpenAI/LiteLLM response
object and a plain dict, which is what lets the loop be driven by a scripted
model in tests without any provider.

The two prompt stanzas below are the only prompt text this package owns. They sit
here, next to the parsers that enforce them, because they are not style: a SUT
whose prompt declares a different shape produces output `parse_final_response`
rejects, and its episodes run to timeout with no error to explain why. Everything
else a SUT tells its model — role, strategy, policy — is that SUT's own business.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

#: Terminal stanza a repair prompt must end with. Parsed by `parse_final_response`.
#: `diagnosis` is the intermediate step Hugo's evaluation parameter 13 ("LLM found the
#: problem rate") asks for: the subject's own statement of the fault, judged after the
#: episode against the fault the scenario injected (benchmarks/core/diagnosis.py).
FINAL_STATUS_CONTRACT = """When you have either validated the public success criteria or concluded that no safe repair can be found, return only JSON:
{"status":"completed"|"failed", "summary":"short observable conclusion", "diagnosis":"the fault as you identified it: which device, which configuration object, and what was wrong with it"}"""

#: What the loop asks when the episode ends without the subject's own conclusion
#: (budget, interaction cap, transcript bound, provider error): one tool-less turn
#: for the same `diagnosis`, so a subject that found the fault and ran out of time
#: fixing it is still measured as having found it. Parsed by `parse_diagnosis`.
DIAGNOSIS_REQUEST = """The episode is over and no further tool call will be dispatched. Do not call a tool. From what you observed, state the fault as you identified it: which device, which configuration object, and what was wrong with it. If you found no fault, say so. Return only JSON:
{"diagnosis":"one or two sentences"}"""

#: Terminal stanza a sanity-question prompt must end with. Parsed by `parse_answer`.
ANSWER_CONTRACT = """When ready, return only JSON:
{"answer":"concise answer", "evidence":["short observable facts"]}"""


class ModelActionError(ValueError):
    """The model response cannot be used as an ANI interaction."""


def tool_calls(message: Any) -> list[Any]:
    value = getattr(message, "tool_calls", None)
    if value is None and isinstance(message, dict):
        value = message.get("tool_calls")
    # Preserve malformed calls so the execution loop can reject them explicitly and
    # give the model feedback. Silently dropping them loses the reason and turns one
    # provider-format error into a full-budget timeout.
    return list(value or [])


def message_content(message: Any) -> str:
    value = getattr(message, "content", None)
    if value is None and isinstance(message, dict):
        value = message.get("content")
    return str(value or "").strip()


def tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any], str]:
    function = getattr(tool_call, "function", None)
    if function is None and isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
    name = getattr(function, "name", None) if not isinstance(function, dict) else function.get("name")
    arguments = getattr(function, "arguments", None) if not isinstance(function, dict) else function.get("arguments")
    if not name:
        raise ModelActionError("ANI tool call has no function name")
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ModelActionError(f"ANI tool {name} has invalid JSON arguments") from exc
    identifier = getattr(tool_call, "id", None) if not isinstance(tool_call, dict) else tool_call.get("id")
    return str(name), parsed, str(identifier or f"ani_call_{time.monotonic_ns()}")


def assistant_tool_message(content: str, calls: list[Any],
                          parts: list[tuple[str, dict[str, Any], str]] | None = None) -> dict[str, Any]:
    """The assistant turn to replay, carrying the ids the tool responses will answer.

    `parts` is the already-parsed (name, arguments, id) of the same calls. It matters
    when a provider sends a tool call with no id of its own: `tool_call_parts` then mints
    one from the clock, and parsing the same call twice mints two, leaving the tool
    response answering an id the assistant turn never used.
    """
    serialized = []
    for index, tool_call in enumerate(calls):
        if parts is not None and index < len(parts):
            name, arguments, identifier = parts[index]
        else:
            name, arguments, identifier = tool_call_parts(tool_call)
        serialized.append({
            "id": identifier,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        })
    return {"role": "assistant", "content": content or None, "tool_calls": serialized}


def parse_final_response(content: str) -> dict[str, Any] | None:
    payload = json_object(content)
    if not isinstance(payload, dict) or payload.get("status") not in {"completed", "failed"}:
        return None
    return {
        "status": str(payload["status"]),
        "summary": str(payload.get("summary") or ""),
        # None, not "", when the model left it out: a record has to tell a subject
        # that stated no diagnosis from one that stated an empty one.
        "diagnosis": _text_or_none(payload.get("diagnosis")),
    }


def parse_diagnosis(content: str) -> str | None:
    """The diagnosis a tool-less turn returned, or None when it returned none.

    The JSON shape `DIAGNOSIS_REQUEST` asks for is read first. A model that answers
    in prose instead still answered the question, and the judge that reads it
    (benchmarks/core/diagnosis.py) compares meaning, not format, so the prose is
    kept as the diagnosis rather than thrown away for its shape.
    """
    payload = json_object(content)
    if isinstance(payload, dict):
        return _text_or_none(payload.get("diagnosis"))
    return _text_or_none(content)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_answer(content: str) -> dict[str, Any] | None:
    payload = json_object(content)
    if not isinstance(payload, dict) or "answer" not in payload:
        return None
    evidence = payload.get("evidence")
    return {"answer": str(payload.get("answer") or ""), "evidence": evidence if isinstance(evidence, list) else []}


def json_object(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def message_to_dict(message: Any) -> dict[str, Any]:
    """The message as the trace records it, with whatever the provider put on it.

    `model_dump` keeps the fields that are not part of this contract — a provider that
    returns its reasoning apart from the answer puts it in one of them — and a message
    that is already a dict is copied for the same reason: reconstructing it from the two
    accessors would drop everything the loop does not read.
    """
    if hasattr(message, "model_dump"):
        try:
            return message.model_dump()
        except Exception:
            pass
    if isinstance(message, dict):
        return {**message, "tool_calls": [str(item) for item in tool_calls(message)]}
    return {"content": message_content(message), "tool_calls": [str(item) for item in tool_calls(message)]}
