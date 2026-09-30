"""Tool-argument transport for XML tool-call parsers, lossless on both sides.

vLLM's ``qwen3_xml`` parser (the Qwen3.5 tool format) types each parameter from the
tool schema's ``properties[name]["type"]`` and falls back to string when that key is
absent (``Qwen3XMLToolParser._get_param_type``, vLLM 0.13). A pydantic optional
container, ``dict[str, Any] | None`` or ``list[str] | None``, is emitted as
``anyOf: [{type: object}, {type: null}]`` with no ``type`` key, so the object the model
wrote inside the parameter tag reaches the subject as a JSON *string* and fails the
tool's own validation. E1 (2026-09-16) measured 766 LangChain ``get_state`` failures
in exactly this shape; the same parser, run offline on the same model text, returns a
dict once the schema says ``type: object``.

Two fixes, both on the transport and neither touching what a tool accepts:
``declare_nullable_types`` gives the optional container an explicit ``type`` so the
parser emits JSON, and ``decode_json_string_arguments`` decodes a JSON string that
still arrives for a container-typed parameter (another parser, another version). A
scalar, a malformed string or a string for a string-typed parameter is left alone, so
a tool's refusals are exactly what they were.
"""
from __future__ import annotations

import json
from typing import Any

#: Recorded in each subject's ``model_parameters`` so a record says which transport
#: its tool calls went through.
TOOL_ARGUMENT_TRANSPORT = "typed_nullable_schema+json_string_decode"

_NULL = {"type": "null"}
_CONTAINERS: dict[str, type] = {"object": dict, "array": list}


def declare_nullable_types(schema: Any) -> Any:
    """``{"anyOf": [X, {"type": "null"}], ...}`` becomes ``X`` with the sibling keys kept.

    Applied recursively, so a nested optional (a term's ``datatype``) is typed too.
    A union of two real types is not touched: there is no single type to declare.
    """
    if isinstance(schema, list):
        return [declare_nullable_types(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    branches = schema.get("anyOf")
    if isinstance(branches, list) and _NULL in branches:
        others = [branch for branch in branches if branch != _NULL]
        if len(others) == 1 and isinstance(others[0], dict):
            merged = dict(others[0])
            # The field's own description and default win over the branch's.
            merged.update({key: value for key, value in schema.items() if key != "anyOf"})
            return declare_nullable_types(merged)
    return {key: declare_nullable_types(value) for key, value in schema.items()}


def decode_json_string_arguments(
    arguments: dict[str, Any], parameters: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Decode a JSON string that arrived for an object- or array-typed parameter.

    Returns the arguments (a new dict) and the names that were decoded. A string is
    replaced only when it parses to the container the schema declares; anything
    else, including a scalar string for a string parameter, is returned as it came.
    """
    properties = (parameters or {}).get("properties") or {}
    decoded: list[str] = []
    result = dict(arguments)
    for name, value in arguments.items():
        if not isinstance(value, str):
            continue
        spec = declare_nullable_types(properties.get(name))
        wanted = _CONTAINERS.get(str(spec.get("type"))) if isinstance(spec, dict) else None
        if wanted is None:
            continue
        text = value.strip()
        if not text or text[0] not in "[{":
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if isinstance(parsed, wanted):
            result[name] = parsed
            decoded.append(name)
    return result, decoded
