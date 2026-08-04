import base64

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import make_guard


def app_with_guard(auth_file: str) -> TestClient:
    app = FastAPI()

    @app.get("/ping", dependencies=[Depends(make_guard(auth_file))])
    def ping():
        return {"ok": True}

    return TestClient(app)


def basic(user, pw):
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def write_auth(tmp_path, user="u1", pw="p1"):
    p = tmp_path / "api-auth.env"
    p.write_text(f"OPENBB_API_AUTH=true\nOPENBB_API_USERNAME={user}\nOPENBB_API_PASSWORD={pw}\n")
    return str(p)


class TestGuard:
    def test_no_header_401(self, tmp_path):
        c = app_with_guard(write_auth(tmp_path))
        assert c.get("/ping").status_code == 401

    def test_wrong_password_401(self, tmp_path):
        c = app_with_guard(write_auth(tmp_path))
        assert c.get("/ping", headers=basic("u1", "wrong")).status_code == 401

    def test_correct_credentials_200(self, tmp_path):
        c = app_with_guard(write_auth(tmp_path))
        assert c.get("/ping", headers=basic("u1", "p1")).status_code == 200

    def test_missing_auth_file_denies_all(self, tmp_path):
        c = app_with_guard(str(tmp_path / "absent.env"))
        assert c.get("/ping", headers=basic("u1", "p1")).status_code == 401
