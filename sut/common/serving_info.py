"""Ask the model endpoint what hardware it is running on.

A wall-clock execution budget is only comparable across episodes that were served
by comparable hardware, and nothing else in the artifact records that. vLLM
answers `/server_info` with its own engine configuration and the host's device
inventory, so the SUT can record the serving side without the operator typing it
in and without the harness knowing anything about a particular cluster.

Every failure here is non-fatal: an endpoint that does not implement the route,
a proxy in the way, or a slow server all yield None, and the episode proceeds
with no serving block rather than not at all.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

TIMEOUT_SECONDS = 5.0

# vllm_config is large and mostly irrelevant; these are the fields that change
# what a model can do inside a fixed budget, plus the ones that decide whether a
# quantised checkpoint can run correctly on the device at all.
_ENGINE_FIELDS = (
    "quantization",
    "dtype",
    "max_model_len",
    "gpu_memory_utilization",
    "kv_cache_dtype",
    "tensor_parallel_size",
)
_SYSTEM_FIELDS = (
    "nvidia_gpu_models",
    "nvidia_driver_version",
    "cuda_runtime_version",
    "torch_version",
    "vllm_version",
)


def _endpoint(api_base: str) -> str:
    base = api_base.rstrip("/")
    # api_base points at the OpenAI-compatible prefix; server_info sits beside it.
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/server_info?config_format=json"


def _dig(config: Any, field: str) -> Any:
    """Find a field in vllm_config, which nests differently across versions."""
    if not isinstance(config, Mapping):
        return None
    if field in config:
        return config[field]
    for value in config.values():
        if isinstance(value, Mapping):
            found = _dig(value, field)
            if found is not None:
                return found
    return None


def _get_json(url: str) -> Any:
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _from_server_info(api_base: str) -> dict[str, Any]:
    """The full answer, when the server exposes it.

    vLLM only mounts /server_info under VLLM_SERVER_DEV_MODE, so this returns
    nothing on a default deployment and the caller falls back.
    """
    payload = _get_json(_endpoint(api_base))
    if not isinstance(payload, Mapping):
        return {}
    serving: dict[str, Any] = {}
    system = payload.get("system_env")
    if isinstance(system, Mapping):
        for field in _SYSTEM_FIELDS:
            value = system.get(field)
            if value not in (None, ""):
                serving[field] = value
    config = payload.get("vllm_config")
    for field in _ENGINE_FIELDS:
        value = _dig(config, field)
        if value not in (None, ""):
            serving[field] = value if isinstance(value, (int, float, bool)) else str(value)
    return serving


def _from_models(api_base: str) -> dict[str, Any]:
    """What every OpenAI-compatible endpoint answers.

    Far less than /server_info, but it is always there, so a run against a
    default deployment still records the context window it was given rather than
    nothing at all.
    """
    base = api_base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    payload = _get_json(f"{base}/models")
    if not isinstance(payload, Mapping):
        return {}
    entries = [item for item in (payload.get("data") or []) if isinstance(item, Mapping)]
    if not entries:
        return {}
    serving: dict[str, Any] = {}
    for field in ("max_model_len", "root", "owned_by"):
        value = entries[0].get(field)
        if value not in (None, ""):
            serving[field] = value
    names = [str(item["id"]) for item in entries if item.get("id")]
    if names:
        serving["served_model_names"] = names
    return serving


# Set by whoever launches the model server. A default vLLM deployment reports no
# device at all -- /server_info is the only route that does and it is mounted only
# under VLLM_SERVER_DEV_MODE -- so without a channel like this the harness has no
# way to record the hardware even when the operator knows exactly what it is.
_ENV_PREFIX = "IBN_SERVING_"


def _from_environment() -> dict[str, Any]:
    """Read IBN_SERVING_* overrides, e.g. IBN_SERVING_NODE, IBN_SERVING_GPU."""
    serving: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX) or not value:
            continue
        serving[key[len(_ENV_PREFIX):].lower()] = value
    return serving


def probe_serving(api_base: str | None) -> dict[str, Any] | None:
    """Describe the serving side, using whatever the endpoint is willing to say.

    Sources are tried richest first and merged, so enabling dev mode upgrades the
    record without changing anything here, and a server that offers neither leaves
    the block absent rather than failing the episode.
    """
    if not api_base:
        return None
    serving: dict[str, Any] = {}
    serving.update(_from_models(api_base))
    # /server_info wins over /v1/models: it reports the engine's own configuration.
    serving.update(_from_server_info(api_base))
    # The environment wins over both. It is the only source for anything the
    # server does not report, and an operator who sets it means it.
    serving.update(_from_environment())
    return serving or None
