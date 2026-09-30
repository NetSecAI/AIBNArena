"""Official A2A SDK transport shared by every benchmark domain."""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Mapping

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest


@dataclass(frozen=True)
class A2ASubject:
    url: str
    timeout_seconds: float


class A2AClient:
    """Resolve an agent card and exchange one JSON text task via the SDK."""

    sdk_version = version("a2a-sdk")

    def __init__(
        self,
        subject: A2ASubject,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.subject = subject
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._client: Any = None
        self.agent_card: Any = None

    async def __aenter__(self) -> "A2AClient":
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.subject.timeout_seconds)
        resolver = A2ACardResolver(self._http_client, self.subject.url)
        self.agent_card = await resolver.get_agent_card(
            http_kwargs={"timeout": self.subject.timeout_seconds}
        )
        config = ClientConfig(
            streaming=True,
            httpx_client=self._http_client,
            accepted_output_modes=["text/plain"],
        )
        self._client = await create_client(self.agent_card, client_config=config)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._client = None
        self.agent_card = None
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def request(self, payload: Mapping[str, Any]) -> str:
        if self._client is None:
            raise RuntimeError("A2A client has not been started")
        request = SendMessageRequest(
            message=new_text_message(json.dumps(payload), role=Role.ROLE_USER)
        )
        latest_text = ""
        async for response in self._client.send_message(request):
            extracted = _response_text(response)
            if extracted:
                latest_text = extracted
        if not latest_text:
            raise ValueError("subject agent returned no text response")
        return latest_text


def _response_text(response: Any) -> str:
    return get_stream_response_text(response).strip()
