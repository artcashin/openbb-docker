import asyncio

import httpx

from app.probes import run_probes

REAL_KEY = "sekret-value-123"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(values, handler):
    async def go():
        async with _client(handler) as c:
            return await run_probes(values, client=c)

    return asyncio.run(go())


class TestClassification:
    def test_2xx_is_ok(self):
        res = run({"EODHD_API_KEY": REAL_KEY}, lambda r: httpx.Response(200, json=[]))
        assert res["EODHD_API_KEY"].result == "ok"

    def test_401_and_403_are_auth_failed(self):
        for code in (401, 403):
            res = run({"EODHD_API_KEY": REAL_KEY}, lambda r, c=code: httpx.Response(c))
            assert res["EODHD_API_KEY"].result == "auth_failed"

    def test_2xx_with_invalid_marker_is_auth_failed(self):
        res = run(
            {"BLS_API_KEY": REAL_KEY},
            lambda r: httpx.Response(200, json={"status": "REQUEST_NOT_PROCESSED"}),
        )
        assert res["BLS_API_KEY"].result == "auth_failed"

    def test_network_error_is_error(self):
        def boom(request):
            raise httpx.ConnectError("nope", request=request)

        res = run({"EODHD_API_KEY": REAL_KEY}, boom)
        assert res["EODHD_API_KEY"].result == "error"

    def test_missing_key_is_skipped(self):
        res = run({}, lambda r: httpx.Response(200))
        assert res["EODHD_API_KEY"].result == "skipped"

    def test_provider_without_probe_is_skipped(self):
        res = run({"BENZINGA_API_KEY": REAL_KEY}, lambda r: httpx.Response(200))
        assert res["BENZINGA_API_KEY"].result == "skipped"


class TestSecrecy:
    def test_key_value_never_in_detail(self):
        def boom(request):
            raise httpx.ConnectError(f"failed url {request.url}", request=request)

        res = run({"EODHD_API_KEY": REAL_KEY}, boom)
        for tr in res.values():
            assert REAL_KEY not in tr.detail

    def test_detail_mentions_status_for_ok(self):
        res = run({"EODHD_API_KEY": REAL_KEY}, lambda r: httpx.Response(200, json=[]))
        assert "200" in res["EODHD_API_KEY"].detail
