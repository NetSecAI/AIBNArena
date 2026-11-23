"""The endpoint store: what it keeps, what it never hands out."""
import json
import stat

import httpx
import pytest

from fastapi.testclient import TestClient

from webui import endpoints
from webui.app import app
from webui.endpoints import (
    OPENAI_API_BASE, Endpoint, ModelListUnavailable, ModelNotServed, credentials, delete, identifier,
    load, openai_endpoint, openai_models, probe_model, put, save,
)

PROXY = Endpoint(id="proxy", name="Internal proxy", api_base="http://127.0.0.1:4000/v1",
                 models=("openai/gpt-5.4", "openai/gpt-4o-mini"), api_key="secret-key")


def test_an_endpoint_survives_a_round_trip(tmp_path):
    store = tmp_path / "models.json"
    save([PROXY], store)
    assert load(store) == [PROXY]


def test_the_file_is_readable_by_its_owner_alone(tmp_path):
    store = tmp_path / "models.json"
    save([PROXY], store)
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    # The key is on disk, which is the point of the file; nothing else may read it.
    assert "secret-key" in store.read_text(encoding="utf-8")


def test_what_the_browser_receives_says_a_key_is_set_but_never_which():
    public = PROXY.public()
    assert public["has_key"] is True
    assert "secret-key" not in json.dumps(public)
    assert "api_key" not in public


def test_editing_an_endpoint_keeps_the_key_it_did_not_resend(tmp_path):
    store = tmp_path / "models.json"
    put(PROXY, store)
    put(Endpoint(id="proxy", name="Renamed", api_base=PROXY.api_base,
                 models=PROXY.models, api_key=None), store)
    stored = load(store)[0]
    assert stored.name == "Renamed"
    assert stored.api_key == "secret-key"


def test_an_empty_key_clears_the_stored_one(tmp_path):
    store = tmp_path / "models.json"
    put(PROXY, store)
    put(Endpoint(id="proxy", name=PROXY.name, api_base=PROXY.api_base,
                 models=PROXY.models, api_key=""), store)
    assert load(store)[0].api_key is None


def test_a_model_finds_the_endpoint_that_serves_it(tmp_path):
    store = tmp_path / "models.json"
    put(PROXY, store)
    assert credentials("openai/gpt-5.4", store).id == "proxy"
    assert credentials("openai/gpt-4o-mini", store).api_key == "secret-key"
    assert credentials("something/unregistered", store) is None


def test_deleting_says_whether_there_was_anything_to_delete(tmp_path):
    store = tmp_path / "models.json"
    put(PROXY, store)
    assert delete("proxy", store)
    assert not delete("proxy", store)
    assert load(store) == []


def test_a_name_becomes_a_usable_identifier():
    assert identifier("Internal LiteLLM proxy") == "internal-litellm-proxy"
    assert identifier("  !!  ") == "endpoint"


def _answering(status=200, error=None):
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-key"
        model = json.loads(request.content)["model"]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": error}})
        return httpx.Response(200, json={"model": model, "choices": []})
    return httpx.MockTransport(handler)


def test_a_model_that_answers_is_named_as_the_endpoint_receives_it():
    line = probe_model("openai/gpt-5.4", PROXY, transport=_answering())
    assert line == "model openai/gpt-5.4: Internal proxy answers as 'gpt-5.4'"


def test_a_refused_model_stops_the_run_with_the_endpoints_reason():
    denied = "key not allowed to access model. This key can only access models=['fr-gpt-5.4']"
    with pytest.raises(ModelNotServed) as caught:
        probe_model("openai/gpt-5.4", PROXY, transport=_answering(403, denied))
    assert "'gpt-5.4'" in str(caught.value)
    assert denied in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_an_endpoint_that_cannot_answer_now_proves_nothing_and_does_not_stop_the_run(status):
    line = probe_model("openai/gpt-5.4", PROXY, transport=_answering(status, "busy"))
    assert line.startswith("model openai/gpt-5.4: not checked")


# --- OpenAI's own API: no URL to type, models chosen from those the key can use ---

def test_an_openai_endpoint_has_openais_url_and_litellms_prefix():
    entry = openai_endpoint("openai", "OpenAI", ("gpt-5.4", "openai/gpt-4o-mini", "gpt-5.4"), "sk-test")
    assert entry.provider == "openai"
    assert entry.api_base == OPENAI_API_BASE
    # Typed with or without the prefix, a model is stored once, as the launcher offers it.
    assert entry.models == ("openai/gpt-5.4", "openai/gpt-4o-mini")
    assert entry.public()["provider"] == "openai"


def test_an_entry_stored_before_the_type_existed_is_a_compatible_endpoint(tmp_path):
    store = tmp_path / "models.json"
    store.write_text(json.dumps({"endpoints": [
        {"id": "proxy", "name": "Proxy", "api_base": "http://127.0.0.1:4000/v1", "models": ["m"]}]}))
    assert load(store)[0].provider == "custom"


def test_the_type_survives_a_round_trip(tmp_path):
    store = tmp_path / "models.json"
    entry = openai_endpoint("openai", "OpenAI", ("gpt-5.4",), "sk-test")
    save([entry, PROXY], store)
    assert load(store) == [entry, PROXY]


def _listing(status=200, ids=()):
    def handler(request):
        assert str(request.url) == f"{OPENAI_API_BASE}/models"
        assert request.headers["authorization"] == "Bearer sk-test"
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "Incorrect API key provided"}})
        return httpx.Response(200, json={"object": "list", "data": [{"id": name} for name in ids]})
    return httpx.MockTransport(handler)


def test_the_models_of_a_key_are_its_chat_models_sorted():
    listed = ["text-embedding-3-small", "gpt-4o-mini", "whisper-1", "o3-mini", "gpt-5.4", "dall-e-3",
              "gpt-4o-mini-tts", "gpt-4o-realtime-preview", "omni-moderation-latest", "gpt-image-1",
              "babbage-002", "gpt-3.5-turbo-instruct", "chatgpt-4o-latest", "tts-1"]
    assert openai_models("sk-test", transport=_listing(ids=listed)) == [
        "chatgpt-4o-latest", "gpt-4o-mini", "gpt-5.4", "o3-mini"]


def test_a_refused_key_says_so_with_openais_reason():
    with pytest.raises(ModelListUnavailable) as caught:
        openai_models("sk-test", transport=_listing(status=401))
    assert "HTTP 401" in str(caught.value)
    assert "Incorrect API key provided" in str(caught.value)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    monkeypatch.setattr(endpoints, "STORE", path)
    return path


def test_the_page_saves_an_openai_endpoint_without_a_url(store):
    client = TestClient(app)
    answer = client.put("/api/endpoints", json={
        "provider": "openai", "name": "", "models": ["gpt-5.4"], "api_key": "sk-test"})
    assert answer.status_code == 200, answer.text
    public = answer.json()
    assert public == {"id": "openai", "name": "OpenAI", "provider": "openai", "api_base": OPENAI_API_BASE,
                      "models": ["openai/gpt-5.4"], "thinking": {}, "has_key": True}
    assert "sk-test" not in answer.text
    assert credentials("openai/gpt-5.4", store).api_key == "sk-test"


def test_an_openai_endpoint_without_a_key_is_refused(store):
    client = TestClient(app)
    answer = client.put("/api/endpoints", json={"provider": "openai", "models": ["gpt-5.4"]})
    assert answer.status_code == 400
    assert "API key" in answer.json()["detail"]
    assert not store.exists()


def test_editing_an_openai_endpoint_keeps_its_key_and_its_url(store):
    client = TestClient(app)
    client.put("/api/endpoints", json={"provider": "openai", "models": ["gpt-5.4"], "api_key": "sk-test"})
    answer = client.put("/api/endpoints", json={
        "id": "openai", "provider": "openai", "name": "OpenAI", "api_base": "http://elsewhere/v1",
        "models": ["gpt-5.4", "gpt-4o-mini"]})
    assert answer.status_code == 200, answer.text
    stored = load(store)[0]
    assert stored.api_key == "sk-test"
    assert stored.api_base == OPENAI_API_BASE
    assert stored.models == ("openai/gpt-5.4", "openai/gpt-4o-mini")


def test_a_compatible_endpoint_still_needs_its_url(store):
    client = TestClient(app)
    answer = client.put("/api/endpoints", json={"name": "Proxy", "models": ["m"]})
    assert answer.status_code == 400
    assert "base URL" in answer.json()["detail"]


def test_the_page_lists_a_stored_keys_models_without_receiving_the_key(store, monkeypatch):
    seen = []
    monkeypatch.setattr(endpoints, "openai_models", lambda key: seen.append(key) or ["gpt-5.4"])
    save([openai_endpoint("openai", "OpenAI", (), "sk-test")], store)
    client = TestClient(app)
    answer = client.post("/api/openai/models", json={"id": "openai"})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"models": ["gpt-5.4"]}
    assert seen == ["sk-test"]
    assert "sk-test" not in answer.text


def test_listing_models_needs_a_key_and_reports_openais_refusal(store, monkeypatch):
    client = TestClient(app)
    assert client.post("/api/openai/models", json={}).status_code == 400

    def refuse(key):
        raise ModelListUnavailable("OpenAI refused the key: HTTP 401 Incorrect API key provided")
    monkeypatch.setattr(endpoints, "openai_models", refuse)
    answer = client.post("/api/openai/models", json={"api_key": "sk-wrong"})
    assert answer.status_code == 502
    assert "HTTP 401" in answer.json()["detail"]
    assert "sk-wrong" not in answer.text
