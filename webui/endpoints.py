"""The model endpoints this interface can run a subject against.

One entry is a provider -- a base URL and the key it takes -- and the models it
serves, because a LiteLLM or vLLM proxy serves many models behind one key.

The file holding them is the only place this tool writes a secret. It stays out
of git, is written readable by its owner alone, and its keys never leave this
process: what the browser receives says whether a key is set, never what it is.
A key is also never put on a command line, where the run log and `ps` would both
show it; it reaches the subject through its environment.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from webui.config import REPOSITORY

STORE = REPOSITORY / "webui" / "models.json"


@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str
    api_base: str
    models: tuple[str, ...] = ()
    api_key: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, object]:
        """What the browser is told: everything but the key."""
        return {
            "id": self.id,
            "name": self.name,
            "api_base": self.api_base,
            "models": list(self.models),
            "has_key": bool(self.api_key),
        }


def identifier(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "endpoint"


def load(store: Path | None = None) -> list[Endpoint]:
    store = store or STORE
    if not store.is_file():
        return []
    document = json.loads(store.read_text(encoding="utf-8"))
    return [
        Endpoint(
            id=str(entry["id"]),
            name=str(entry["name"]),
            api_base=str(entry["api_base"]),
            models=tuple(str(model) for model in entry.get("models") or ()),
            api_key=entry.get("api_key") or None,
        )
        for entry in document.get("endpoints") or []
    ]


def save(endpoints: list[Endpoint], store: Path | None = None) -> None:
    """Write the file as its owner alone can read, and replace it in one step."""
    store = store or STORE
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {"endpoints": [
        {"id": item.id, "name": item.name, "api_base": item.api_base,
         "models": list(item.models), "api_key": item.api_key}
        for item in endpoints
    ]}
    handle, temporary = tempfile.mkstemp(dir=store.parent, prefix=".models.", suffix=".tmp")
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
        os.replace(temporary, store)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def put(entry: Endpoint, store: Path | None = None) -> Endpoint:
    """Add this endpoint or replace the one with its id, keeping a key it omits.

    An omitted key (`None`) means "leave the stored one alone", so renaming an
    endpoint does not silently drop the credential it runs with. An empty key
    means the person cleared it.
    """
    endpoints = load(store)
    existing = next((item for item in endpoints if item.id == entry.id), None)
    if entry.api_key is None and existing is not None:
        entry = Endpoint(entry.id, entry.name, entry.api_base, entry.models, existing.api_key)
    endpoints = [item for item in endpoints if item.id != entry.id] + [entry]
    save(sorted(endpoints, key=lambda item: item.name.lower()), store)
    return entry


def delete(identifier_: str, store: Path | None = None) -> bool:
    endpoints = load(store)
    remaining = [item for item in endpoints if item.id != identifier_]
    if len(remaining) == len(endpoints):
        return False
    save(remaining, store)
    return True


def credentials(model: str, store: Path | None = None) -> Endpoint | None:
    """The endpoint that serves this model, or None when none was registered."""
    return next((item for item in load(store) if model in item.models), None)


class ModelNotServed(RuntimeError):
    """The endpoint refused a request for this model."""


def served_name(model: str) -> str:
    """The name the endpoint receives for this model.

    A subject routes a registered endpoint through LiteLLM's `openai/` provider
    (`sut.common.litellm_backend.litellm_model_name`), which strips that prefix
    before the request: `openai/gpt-5.4` asks the endpoint for `gpt-5.4`.
    """
    return model.removeprefix("openai/")


def probe_model(
    model: str,
    endpoint: Endpoint,
    *,
    timeout: float = 60.0,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Ask the endpoint for one short answer from this model; raise if it refuses.

    Done before the subject starts, because a model the endpoint refuses only
    fails at the first model call, minutes into the episode, after the testbed
    was deployed and the fault injected. A real request and not `/models`: a
    proxy may answer for an alias it does not list (LiteLLM serves `gpt-5.4`
    while listing only `fr-gpt-5.4`). No token limit is sent, because models
    disagree on which parameter carries it and a rejected parameter would read
    as a rejected model. Only a refusal (a 4xx other than rate limiting) stops
    the run; an endpoint that cannot be reached proves nothing either way.
    Returns the line the launcher logs.
    """
    wanted = served_name(model)
    headers = {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
    url = f"{endpoint.api_base.rstrip('/')}/chat/completions"
    body = {"model": wanted, "messages": [{"role": "user", "content": "Reply with OK."}]}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        return f"model {model}: not checked, {url} did not answer ({type(exc).__name__}: {exc})"
    if 400 <= response.status_code < 500 and response.status_code != 429:
        raise ModelNotServed(
            f"{endpoint.name} refused model {model} (sent as {wanted!r}): "
            f"HTTP {response.status_code} {_error_message(response)}"
        )
    if response.status_code >= 400:
        return f"model {model}: not checked, {url} answered HTTP {response.status_code}"
    return f"model {model}: {endpoint.name} answers as {wanted!r}"


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text[:300]
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or response.text)[:300]
