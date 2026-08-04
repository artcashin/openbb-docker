"""Concurrent provider probes. Detail strings are built ONLY from status codes
and exception class names — never from URLs, bodies, or key material."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.registry import PROVIDERS

TIMEOUT_S = 5.0


@dataclass(frozen=True)
class TestResult:
    result: str  # ok | auth_failed | error | skipped
    detail: str


async def _probe_one(client: httpx.AsyncClient, env_var: str, values: dict[str, str]) -> TestResult:
    provider = PROVIDERS[env_var]
    if provider.probe is None:
        return TestResult("skipped", "no probe defined")
    request = provider.probe.build(values)
    if request is None:
        return TestResult("skipped", "key not set")
    try:
        resp = await client.send(request)
    except httpx.HTTPError as e:
        return TestResult("error", type(e).__name__)
    if resp.status_code in (401, 403):
        return TestResult("auth_failed", f"HTTP {resp.status_code}")
    if resp.is_success:
        body = resp.text[:2000]
        for marker in provider.probe.invalid_markers:
            if marker in body:
                return TestResult("auth_failed", f"HTTP {resp.status_code}, rejected by body")
        return TestResult("ok", f"HTTP {resp.status_code}")
    return TestResult("error", f"HTTP {resp.status_code}")


async def run_probes(
    values: dict[str, str], client: httpx.AsyncClient | None = None
) -> dict[str, TestResult]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        names = list(PROVIDERS)
        results = await asyncio.gather(*(_probe_one(client, v, values) for v in names))
        return dict(zip(names, results))
    finally:
        if own:
            await client.aclose()
