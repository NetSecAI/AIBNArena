"""The model endpoints this interface can run a subject against.

One entry is a provider -- a base URL and the key it takes -- and the models it
serves, because a LiteLLM or vLLM proxy serves many models behind one key. Its type
says which: `openai` is OpenAI's own API, whose URL is fixed (`OPENAI_API_BASE`) and
whose models are listed from the key (`openai_models`); `openrouter` is OpenRouter,
whose URL is fixed too (`OPENROUTER_API_BASE`) and whose models are listed from its
catalog (`openrouter_models`); `custom` is any other OpenAI-compatible endpoint, at the
URL given.

Each model of an endpoint may also carry how it is asked to think (`thinking`:
`enable_thinking`, `thinking_effort`, `thinking_style`), chosen when the model is
declared and nowhere else. A launch writes it into the request it records
(`with_thinking`), so a record says what its subject was asked, and it is checked
against the model's family before anything starts (`check_thinking`).

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
from dataclasses import dataclass, field, replace
from pathlib import Path

from typing import Any, Mapping

import httpx

from sut.common.thinking import ThinkingUnsupported, resolve_thinking
from webui.config import REPOSITORY

STORE = REPOSITORY / "webui" / "models.json"

#: OpenAI's own API: an `openai` endpoint has this URL whatever was typed.
OPENAI_API_BASE = "https://api.openai.com/v1"
#: OpenRouter's API, OpenAI-compatible: an `openrouter` endpoint has this URL whatever was typed.
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
#: The endpoint types whose URL is fixed, so the page asks for none.
FIXED_API_BASES = {"openai": OPENAI_API_BASE, "openrouter": OPENROUTER_API_BASE}
#: How the page names each type, in what it answers.
PROVIDER_NAMES = {"openai": "OpenAI", "openrouter": "OpenRouter", "custom": "endpoint"}
#: The prefix a model id takes for LiteLLM's OpenAI provider (`served_name` strips it).
OPENAI_PREFIX = "openai/"

#: How a model is asked to think, as a subject's settings name it.
THINKING_FIELDS = ("enable_thinking", "thinking_effort", "thinking_style")


def clean_thinking(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """The thinking fields a model's settings state, empty values dropped: an unset
    field leaves the subject's own default, it is not a setting."""
    cleaned: dict[str, Any] = {}
    for name in THINKING_FIELDS:
        value = (settings or {}).get(name)
        if value is None or value == "":
            continue
        if name == "enable_thinking":
            if isinstance(value, str):
                value = {"true": True, "false": False}.get(value.lower(), value)
            if not isinstance(value, bool):
                raise ValueError(f"enable_thinking must be true or false, not {value!r}")
        else:
            value = str(value)
        cleaned[name] = value
    return cleaned


def check_thinking(model: str, settings: Mapping[str, Any] | None) -> None:
    """Refuse, by the reason the subject would give at startup, thinking this model's
    family cannot be asked for (`sut.common.thinking.resolve_thinking`)."""
    settings = settings or {}
    try:
        resolve_thinking(model, settings.get("enable_thinking"),
                         settings.get("thinking_style") or "auto", settings.get("thinking_effort"))
    except ThinkingUnsupported as exc:
        raise ValueError(f"thinking for {model}: {exc}") from exc


@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str
    api_base: str
    models: tuple[str, ...] = ()
    api_key: str | None = field(default=None, repr=False)
    #: `openai`, `openrouter` or `custom`; an entry written before the type existed is `custom`.
    provider: str = "custom"
    #: How each model is asked to think, by model id (`clean_thinking`). A model absent
    #: here is run with its subject's own default.
    thinking: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        """What the browser is told: everything but the key."""
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "api_base": self.api_base,
            "models": list(self.models),
            "thinking": {model: dict(settings) for model, settings in self.thinking.items()},
            "has_key": bool(self.api_key),
        }


def _prefixed(model: str) -> str:
    return model if model.startswith(OPENAI_PREFIX) else OPENAI_PREFIX + model


def stored_model(provider: str, model: str) -> str:
    """A model as typed on the page, as the subject is given it.

    Every registered endpoint is reached through LiteLLM's `openai/` provider, which strips
    that prefix before the request (`served_name`). An OpenAI model takes it whether or not
    it was typed with it (`gpt-5` is `openai/gpt-5`). An OpenRouter model always takes it,
    because OpenRouter's own ids may start with `openai/` too: `openai/gpt-4o` there is
    `openai/openai/gpt-4o` here, and OpenRouter receives `openai/gpt-4o`. Any other endpoint's
    model is as typed.
    """
    if provider == "openai":
        return _prefixed(model)
    if provider == "openrouter":
        return OPENAI_PREFIX + model
    return model


def page_model(provider: str, model: str) -> str:
    """The model as the page shows it: `stored_model` undone."""
    return model.removeprefix(OPENAI_PREFIX) if provider in FIXED_API_BASES else model


def provider_endpoint(provider: str, id_: str, name: str, models: tuple[str, ...], api_key: str | None,
                      thinking: Mapping[str, Mapping[str, Any]] | None = None) -> Endpoint:
    """An endpoint of a provider whose URL is fixed (`FIXED_API_BASES`): each model and its
    thinking settings under the name the subject is given (`stored_model`)."""
    named = tuple(dict.fromkeys(stored_model(provider, model) for model in models))
    settings = {stored_model(provider, model): value for model, value in (thinking or {}).items()}
    return Endpoint(id=id_, name=name, api_base=FIXED_API_BASES[provider], models=named, api_key=api_key,
                    provider=provider, thinking=declared_thinking(named, settings))


def openai_endpoint(id_: str, name: str, models: tuple[str, ...], api_key: str | None,
                    thinking: Mapping[str, Mapping[str, Any]] | None = None) -> Endpoint:
    """An endpoint of OpenAI's own API (`provider_endpoint`)."""
    return provider_endpoint("openai", id_, name, models, api_key, thinking)


def declared_thinking(models: tuple[str, ...], thinking: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The non-empty settings of the models this endpoint serves; any other is dropped."""
    kept = {}
    for model in models:
        settings = clean_thinking((thinking or {}).get(model))
        if settings:
            kept[model] = settings
    return kept


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
            provider=str(entry.get("provider") or "custom"),
            thinking=declared_thinking(tuple(str(model) for model in entry.get("models") or ()),
                                  entry.get("thinking")),
        )
        for entry in document.get("endpoints") or []
    ]


def save(endpoints: list[Endpoint], store: Path | None = None) -> None:
    """Write the file as its owner alone can read, and replace it in one step."""
    store = store or STORE
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {"endpoints": [
        {"id": item.id, "name": item.name, "provider": item.provider, "api_base": item.api_base,
         "models": list(item.models), "api_key": item.api_key,
         "thinking": {model: dict(settings) for model, settings in item.thinking.items()}}
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
        entry = replace(entry, api_key=existing.api_key)
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


def thinking_of(model: str, store: Path | None = None) -> dict[str, Any]:
    """How the model was declared to think, or {} for one unregistered or declared silent."""
    endpoint = credentials(model, store)
    return dict(endpoint.thinking.get(model) or {}) if endpoint is not None else {}


def with_thinking(request: Any, settings: Mapping[str, Any]) -> Any:
    """The request with the model's thinking settings in the fields it leaves unset.

    A field the request states wins: a recorded request replayed keeps what it ran
    with. Written into the request itself, which is what the run records.
    """
    update = {name: value for name, value in settings.items()
              if name in THINKING_FIELDS and getattr(request, name, None) is None}
    return request.model_copy(update=update) if update else request


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


#: What an OpenAI model id says it does other than chat, which a subject cannot use.
_NOT_CHAT = re.compile(r"embedding|whisper|tts|dall-e|image|audio|realtime|transcribe|moderation|search|"
                       r"davinci|babbage|instruct|sora|codex", re.IGNORECASE)
#: The families of OpenAI chat models.
_CHAT = re.compile(r"^(gpt-|chatgpt-|o\d)")


class ModelListUnavailable(RuntimeError):
    """The provider did not list its models."""


def openai_models(
    api_key: str,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """The chat models this OpenAI key can use, as OpenAI lists them (`GET /models`), sorted.

    Listed from the key rather than written here, so the page offers what the account
    has today. Embedding, audio, image, moderation and search models are left out: a
    subject talks to its model through chat completions.
    """
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(f"{OPENAI_API_BASE}/models", headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise ModelListUnavailable(f"OpenAI did not answer ({type(exc).__name__}: {exc})") from exc
    if response.status_code >= 400:
        raise ModelListUnavailable(f"OpenAI refused the key: HTTP {response.status_code} {_error_message(response)}")
    try:
        listed = [str(item["id"]) for item in response.json().get("data") or []]
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelListUnavailable("OpenAI's answer is not a model list") from exc
    return sorted(name for name in listed if _CHAT.match(name) and not _NOT_CHAT.search(name))


def openrouter_models(
    api_key: str | None = None,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """The OpenRouter models a subject can run on, as OpenRouter lists them (`GET /models`), sorted.

    Only those that take tools and answer in text: a subject repairs the network through
    tool calls, and a model offered without them could only end its episode with no call.
    The catalog is OpenRouter's whole one; the key, when given, is sent with the request.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(f"{OPENROUTER_API_BASE}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise ModelListUnavailable(f"OpenRouter did not answer ({type(exc).__name__}: {exc})") from exc
    if response.status_code >= 400:
        raise ModelListUnavailable(f"OpenRouter refused: HTTP {response.status_code} {_error_message(response)}")
    try:
        listed = response.json().get("data") or []
        usable = [str(item["id"]) for item in listed
                  if "tools" in (item.get("supported_parameters") or [])
                  and "text" in ((item.get("architecture") or {}).get("output_modalities") or ["text"])]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ModelListUnavailable("OpenRouter's answer is not a model list") from exc
    return sorted(set(usable))
