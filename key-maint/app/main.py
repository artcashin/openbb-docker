"""Entry point: one process, two uvicorn servers. 8446 = admin (published to
the NAS host loopback by compose; reachable only via ssh -L), 8447 = network
(proxied by Tailscale Serve on :10000, funneled)."""
from __future__ import annotations

import asyncio
import os

import uvicorn

from app.server import create_app

ADMIN_PORT = 8446
NETWORK_PORT = 8447


def build_servers(cred_file: str, auth_file: str) -> list[uvicorn.Server]:
    def mk(role: str, port: int) -> uvicorn.Server:
        cfg = uvicorn.Config(
            create_app(role=role, cred_file=cred_file, auth_file=auth_file),
            host="127.0.0.1",
            port=port,
            log_level="info",
        )
        return uvicorn.Server(cfg)

    return [mk("admin", ADMIN_PORT), mk("network", NETWORK_PORT)]


async def _serve_all() -> None:
    cred = os.environ.get("KEYMAINT_CRED_FILE", "/config/credentials.env")
    auth = os.environ.get("KEYMAINT_AUTH_FILE", "/config/api-auth.env")
    await asyncio.gather(*(s.serve() for s in build_servers(cred, auth)))


if __name__ == "__main__":
    asyncio.run(_serve_all())
