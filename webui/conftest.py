"""What every test of the web interface runs under."""
from __future__ import annotations

import pytest

from webui import endpoints


@pytest.fixture(autouse=True)
def _temporary_endpoint_store(tmp_path, monkeypatch):
    """Every test reads and writes an empty endpoint store of its own, never `webui/models.json`.

    The real store is the person's own: the endpoints they registered, their keys, and the
    thinking each model is declared with. A test that read it passed or failed on what was
    registered on that machine (a model declared to think is refused once its family cannot),
    and one that wrote it would change what the next launch runs with. A test that needs
    endpoints registers them in this store, or points `endpoints.STORE` at one of its own.
    """
    monkeypatch.setattr(endpoints, "STORE", tmp_path / "models.json")
