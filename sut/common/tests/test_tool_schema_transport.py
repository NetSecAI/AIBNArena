"""An optional container gets a declared type, and a JSON string for it is decoded."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sut.common.tool_schema_transport import (
    declare_nullable_types,
    decode_json_string_arguments,
)


class Arguments(BaseModel):
    label: str
    properties: dict[str, Any] | None = Field(default=None, description="the map")
    expected_current: list[dict[str, Any]] | None = None
    mode: str | int | None = None
    flag: bool = False


def test_an_optional_container_is_declared_with_its_type_and_keeps_its_description():
    schema = declare_nullable_types(Arguments.model_json_schema())
    props = schema["properties"]
    assert "anyOf" not in props["properties"]
    assert props["properties"]["type"] == "object"
    assert props["properties"]["description"] == "the map"
    assert props["properties"]["default"] is None
    assert props["expected_current"]["type"] == "array"
    assert props["expected_current"]["items"]["type"] == "object"
    # a real union has no single type to declare, and is left as pydantic wrote it
    assert [b.get("type") for b in props["mode"]["anyOf"]] == ["string", "integer", "null"]
    assert props["label"] == {"title": "Label", "type": "string"}
    assert props["flag"]["type"] == "boolean"


def test_declaring_types_returns_a_new_structure_and_leaves_the_input_alone():
    original = Arguments.model_json_schema()
    before = repr(original)
    declare_nullable_types(original)
    assert repr(original) == before


def test_a_json_string_for_a_container_parameter_is_decoded_and_named():
    parameters = Arguments.model_json_schema()
    arguments = {
        "label": "Route",
        "properties": '{"routeProtocol": "static", "routeMetric": "1"}',
        "expected_current": ' [{"kind": "literal", "value": "static"}] ',
        "flag": "true",
    }
    decoded, names = decode_json_string_arguments(arguments, parameters)
    assert decoded["properties"] == {"routeProtocol": "static", "routeMetric": "1"}
    assert decoded["expected_current"] == [{"kind": "literal", "value": "static"}]
    assert names == ["properties", "expected_current"]
    assert decoded["label"] == "Route" and decoded["flag"] == "true", "only containers are decoded"
    assert arguments["properties"].startswith("{"), "the caller's dict is not changed"


def test_what_is_not_a_json_container_of_the_declared_kind_is_left_as_it_came():
    parameters = Arguments.model_json_schema()
    arguments = {
        "label": '{"looks": "like json"}',          # string parameter: never decoded
        "properties": '["a", "list", "not", "a", "map"]',   # wrong container kind
        "expected_current": "[not json",            # malformed
        "flag": "[]",
    }
    decoded, names = decode_json_string_arguments(arguments, parameters)
    assert decoded == arguments and names == []


def test_without_a_schema_nothing_is_decoded():
    decoded, names = decode_json_string_arguments({"properties": "{}"}, None)
    assert decoded == {"properties": "{}"} and names == []
