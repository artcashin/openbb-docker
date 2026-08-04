"""FastAPI app factory. The role fixed at construction decides the tier
ceiling: admin bind = tier 3 always; network bind = tier 2 for tailnet
clients (X-Forwarded-For in CGNAT 100.64/10, as set by tailscaled), tier 1
otherwise — including absent or unparseable XFF (fail toward less
disclosure). Funnel port note: serve proxies :10000 -> this app."""
from __future__ import annotations

import ipaddress

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import make_guard
from app.credfile import load
from app.probes import run_probes
from app.rows import build_rows

_CGNAT = ipaddress.ip_network("100.64.0.0/10")

WIDGETS = {
    "provider_api_keys": {
        "name": "Provider API Keys",
        "description": "State of every provider API key on the backend: set/missing, "
        "public-demo-key highlight, and on-demand live key tests.",
        "category": "Admin",
        "type": "table",
        "endpoint": "keys",
        "gridData": {"w": 30, "h": 12},
        "data": {"dataKey": "rows", "table": {"showAll": True}},
        "params": [
            {
                "paramName": "run_tests",
                "label": "Run live key tests",
                "type": "boolean",
                "value": False,
                "description": "Fire one real request per configured provider "
                "(tailnet/admin connections only).",
            }
        ],
    }
}


def _tier(role: str, request: Request) -> int:
    if role == "admin":
        return 3
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    try:
        if ipaddress.ip_address(xff) in _CGNAT:
            return 2
    except ValueError:
        pass
    return 1


def create_app(role: str, cred_file: str, auth_file: str) -> FastAPI:
    if role not in ("network", "admin"):
        raise ValueError(f"unknown role: {role}")
    app = FastAPI(
        dependencies=[Depends(make_guard(auth_file))],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://pro.openbb.co"],
        allow_methods=["GET", "PUT", "OPTIONS"],
        allow_headers=["Authorization"],
    )

    @app.get("/widgets.json")
    def widgets() -> Response:
        return JSONResponse(WIDGETS)

    @app.get("/keys")
    async def keys(request: Request, run_tests: bool = False) -> Response:
        tier = _tier(role, request)
        values = load(cred_file)
        tests = None
        if run_tests and tier >= 2 and values is not None:
            tests = await run_probes(values)
        return JSONResponse({"tier": tier, "rows": build_rows(values, tier, tests)})

    @app.put("/keys/{env_var}")
    def put_key(env_var: str) -> Response:
        return JSONResponse(
            {"detail": "editing is phase 2; not implemented"}, status_code=501
        )

    return app
