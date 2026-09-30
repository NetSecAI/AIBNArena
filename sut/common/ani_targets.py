"""Model-visible canonical write targets for topology-bound SUT routers.

The generic ANI contract is intentionally topology-independent.  A router that already
wraps a concrete lab ANI can, however, tell its model the exact reviewed node names without
revealing a fault, oracle, link, role, or device state.  This module specializes a copied
``update_config`` schema and validates direct model writes while leaving the ANI itself and
compiler-generated calls untouched.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from benchmarks.platforms.containerlab import ANIRequestError


def freeze_canonical_targets(ani: Any) -> tuple[str, ...]:
    """Capture the concrete lab's exact node names once for a router lifetime."""
    try:
        nodes = ani.env.config.nodes
    except AttributeError as exc:
        raise RuntimeError(
            "a topology-bound router requires ani.env.config.nodes"
        ) from exc
    if not isinstance(nodes, Mapping) or not nodes:
        raise RuntimeError(
            "a topology-bound router requires a non-empty ani.env.config.nodes mapping"
        )
    if not all(isinstance(name, str) and name for name in nodes):
        raise RuntimeError("ANI node names must be non-empty strings")
    return tuple(sorted(nodes))


def schemas_with_canonical_targets(
    schemas: list[dict[str, Any]], targets: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Deep-copy schemas and specialize ``update_config.changes[].target``.

    Failing loudly on drift is deliberate: silently returning an unspecialized target
    string would recreate the model-facing ambiguity this boundary is meant to remove.
    """
    copied = deepcopy(schemas)
    matches = [
        schema
        for schema in copied
        if (schema.get("function") or {}).get("name") == "update_config"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "ANI tool schemas must contain exactly one update_config definition"
        )
    try:
        target_schema = matches[0]["function"]["parameters"]["properties"][
            "changes"
        ]["items"]["properties"]["target"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "update_config schema no longer exposes changes[].target"
        ) from exc
    if not isinstance(target_schema, dict):
        raise RuntimeError("update_config changes[].target must be a JSON schema object")
    target_schema["enum"] = list(targets)
    target_schema["description"] = (
        "Exact canonical lab node name. Allowed values: " + ", ".join(targets)
    )
    return copied


def validate_canonical_update_targets(
    arguments: Any, targets: tuple[str, ...]
) -> None:
    """Reject every unknown direct target before any part of a batch is delegated.

    Other malformed shapes remain the ANI's responsibility.  This preflight only owns
    syntactically present string targets, so it does not create a second ANI validator.
    """
    if not isinstance(arguments, dict):
        return
    changes = arguments.get("changes")
    if not isinstance(changes, list):
        return
    allowed = frozenset(targets)
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            continue
        target = change.get("target")
        if isinstance(target, str) and target not in allowed:
            raise ANIRequestError(
                f"unknown update_config target {target!r} at changes[{index}]; "
                "allowed canonical targets: " + ", ".join(targets)
            )
