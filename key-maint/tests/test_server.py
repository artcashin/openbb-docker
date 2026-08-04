import base64

import pytest
from fastapi.testclient import TestClient

from app.server import create_app

AUTH = {"Authorization": "Basic " + base64.b64encode(b"u1:p1").decode()}


@pytest.fixture
def files(tmp_path):
    cred = tmp_path / "credentials.env"
    cred.write_text("EODHD_API_KEY=demo\nFMP_API_KEY=realkey99\n")
    auth = tmp_path / "api-auth.env"
    auth.write_text("OPENBB_API_USERNAME=u1\nOPENBB_API_PASSWORD=p1\n")
    return str(cred), str(auth)


def client(files, role):
    cred, auth = files
    return TestClient(create_app(role=role, cred_file=cred, auth_file=auth))


def keys(c, headers=None, params=None):
    h = dict(AUTH)
    h.update(headers or {})
    return c.get("/keys", headers=h, params=params or {})


class TestTiers:
    def test_admin_role_is_tier_3_with_values(self, files):
        body = keys(client(files, "admin")).json()
        assert body["tier"] == 3
        fmp = next(r for r in body["rows"] if r["env_var"] == "FMP_API_KEY")
        assert fmp["value"] == "realkey99"

    def test_network_without_xff_is_tier_1(self, files):
        body = keys(client(files, "network")).json()
        assert body["tier"] == 1
        assert all("value" not in r for r in body["rows"])

    def test_network_cgnat_xff_is_tier_2(self, files):
        body = keys(client(files, "network"), headers={"X-Forwarded-For": "100.93.226.48"}).json()
        assert body["tier"] == 2
        assert all("value" not in r for r in body["rows"])

    def test_network_public_xff_is_tier_1(self, files):
        body = keys(client(files, "network"), headers={"X-Forwarded-For": "96.225.78.180"}).json()
        assert body["tier"] == 1

    def test_garbage_xff_is_tier_1(self, files):
        body = keys(client(files, "network"), headers={"X-Forwarded-For": "not-an-ip"}).json()
        assert body["tier"] == 1


class TestRunTests:
    def test_tier_1_ignores_run_tests(self, files, monkeypatch):
        async def fail(*a, **k):
            raise AssertionError("probes must not run at tier 1")

        monkeypatch.setattr("app.server.run_probes", fail)
        r = keys(client(files, "network"), params={"run_tests": "true"})
        assert r.status_code == 200
        assert all("test" not in row for row in r.json()["rows"])

    def test_tier_2_runs_probes(self, files, monkeypatch):
        from app.probes import TestResult

        async def fake(values, client=None):
            return {"FMP_API_KEY": TestResult("ok", "HTTP 200")}

        monkeypatch.setattr("app.server.run_probes", fake)
        r = keys(
            client(files, "network"),
            headers={"X-Forwarded-For": "100.93.226.48"},
            params={"run_tests": "true"},
        )
        fmp = next(x for x in r.json()["rows"] if x["env_var"] == "FMP_API_KEY")
        assert fmp["test"]["result"] == "ok"


class TestContract:
    def test_auth_required(self, files):
        assert client(files, "network").get("/keys").status_code == 401

    def test_widgets_json_shape(self, files):
        w = client(files, "network").get("/widgets.json", headers=AUTH).json()
        entry = w["provider_api_keys"]
        assert entry["type"] == "table"
        assert entry["endpoint"] == "keys"
        names = [p["paramName"] for p in entry["params"]]
        assert names == ["run_tests"]

    def test_put_returns_501(self, files):
        r = client(files, "admin").put("/keys/FMP_API_KEY", headers=AUTH)
        assert r.status_code == 501

    def test_missing_cred_file_still_200(self, files, tmp_path):
        _, auth = files
        c = TestClient(create_app(role="admin", cred_file=str(tmp_path / "x.env"), auth_file=auth))
        r = keys(c)
        assert r.status_code == 200
        assert r.json()["rows"][0]["status"] == "unknown"

    def test_docs_and_openapi_disabled(self, files):
        c = client(files, "network")
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/redoc").status_code == 404

    def test_unknown_role_rejected(self, files):
        cred, auth = files
        with pytest.raises(ValueError):
            create_app(role="bogus", cred_file=cred, auth_file=auth)
