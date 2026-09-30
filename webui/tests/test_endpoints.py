"""The endpoint store: what it keeps, what it never hands out."""
import json
import stat

import httpx
import pytest

from webui.endpoints import (
    Endpoint, ModelNotServed, probe_model, credentials, delete, identifier, load, put, save,
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
