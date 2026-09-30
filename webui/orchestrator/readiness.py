"""Proving the subject answering is the process and model just started.

A listener that answers is not enough: an earlier subject left running on the
same port answers just as well, and the episode would then be attributed to a
model that never ran. The runtime document carries the process id and the
configured model, so both are checked before the judge is pointed at it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

import httpx

RUNTIME_IDENTITY_PATH = "/.well-known/sut-runtime.json"


class ReadinessError(RuntimeError):
    """The subject never became ready, or the one answering is not ours."""


async def wait_for_runtime_identity(
    sut_url: str,
    *,
    expected: Mapping[str, Any],
    timeout: float,
    interval: float,
    is_running: Callable[[], bool],
) -> dict[str, Any]:
    """Poll until the subject answers, then hold its identity to `expected`."""
    deadline = asyncio.get_running_loop().time() + timeout
    url = f"{sut_url.rstrip('/')}{RUNTIME_IDENTITY_PATH}"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            if not is_running():
                raise ReadinessError("the subject exited during startup; see its log")
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise ReadinessError(
                        f"the subject did not answer {url} within {timeout:g} seconds"
                    ) from None
                await asyncio.sleep(interval)
                continue
            document = response.json()
            mismatches = [
                f"{key}: expected {value!r}, received {document.get(key)!r}"
                for key, value in expected.items()
                if document.get(key) != value
            ]
            if mismatches:
                # A listener answered with someone else's identity. Retrying would
                # only wait for it to change its mind, so this stops the episode.
                raise ReadinessError("runtime identity mismatch: " + "; ".join(mismatches))
            return document
