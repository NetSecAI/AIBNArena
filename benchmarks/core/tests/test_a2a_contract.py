from __future__ import annotations

import asyncio
import json
import os
import unittest

import httpx
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from benchmarks.core.a2a import A2AClient, A2ASubject
from sut.common.a2a_app import build_a2a_application


class EchoAgent:
    def invoke(self, task_json: str) -> str:
        task = json.loads(task_json)
        return json.dumps({"received": task, "transport": "official-a2a-sdk"})


class A2AContractTests(unittest.TestCase):
    def test_official_client_and_shared_server_exchange_json(self) -> None:
        async def exercise() -> None:
            card = AgentCard(
                name="contract-test",
                description="in-process A2A contract test",
                supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url="http://test/")],
                version="1.0.0",
                default_input_modes=["text/plain"],
                default_output_modes=["text/plain"],
                capabilities=AgentCapabilities(streaming=True),
                skills=[
                    AgentSkill(
                        id="echo",
                        name="Echo",
                        description="Echo a JSON task",
                        tags=["test"],
                    )
                ],
            )
            app = build_a2a_application(
                EchoAgent(),
                agent_card=card,
                source="test",
                runtime_metadata={
                    "sut_version": "1.0.0",
                    "configured_model": "provider/model",
                },
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as http_client:
                identity_response = await http_client.get("/.well-known/sut-runtime.json")
                self.assertEqual(200, identity_response.status_code)
                self.assertEqual({
                    "schema_version": "1.0",
                    "sut_identity": "test",
                    "process_id": os.getpid(),
                    "sut_version": "1.0.0",
                    "configured_model": "provider/model",
                }, identity_response.json())
                subject = A2ASubject(
                    url="http://test",
                    timeout_seconds=5,
                )
                async with A2AClient(subject, http_client=http_client) as client:
                    response = json.loads(await client.request({"intent": "echo"}))
                    self.assertEqual({"intent": "echo"}, response["received"])
                    self.assertEqual("official-a2a-sdk", response["transport"])
                    self.assertEqual("contract-test", client.agent_card.name)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
