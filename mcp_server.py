#!/usr/bin/env python3
"""MCP server for the Constructor Insight report — read-only access to the
contribution/delivery data over streamable HTTP.

Runs alongside the portal (same DB, same code). Every tool is READ-ONLY; the one
free-form tool (sql_query) refuses anything but a single SELECT/WITH and runs with
PRAGMA query_only. Auth: set MCP_TOKEN and clients must send
`Authorization: Bearer <token>`; unset = open (only safe behind a reverse proxy or
on localhost) and the server warns loudly at startup.

    MCP_TOKEN=… MCP_PORT=8082 python mcp_server.py        # serves /mcp

Reuses store.py (aggregate + granular tables), semantic_metrics (taxonomy-derived
delivery), discovery (scope→repos) and semantic (effective taxonomy).
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import store
import tooldefs

# Behind the reverse proxy the Host header is the public host, which the default
# DNS-rebinding protection would reject (421). We sit behind nginx + bearer auth,
# so that protection is redundant — disable it and let the token gate access.
mcp = FastMCP("constructor-fabric-report",
              transport_security=TransportSecuritySettings(
                  enable_dns_rebinding_protection=False))

# Tool bodies live in tooldefs.py (shared with the in-process metrics chat). Register
# each here so FastMCP derives its schema from the function's signature + docstring,
# and re-export them at module level so callers can invoke them directly (tests,
# scripts) as mcp_server.<tool>(...).
for _fn in tooldefs.TOOLS:
    mcp.tool()(_fn)
globals().update(tooldefs.DISPATCH)


# ---- ASGI app + token auth -------------------------------------------------
def _current_token() -> str:
    """The active bearer token. DB secret 'mcp_token' (set from the portal) wins, so
    the UI can change it live; MCP_TOKEN env is a fallback. Empty = unauthenticated."""
    try:
        conn = store.connect()
        v = store.get_secret(conn, "mcp_token") or ""
        conn.close()
        if v:
            return v
    except Exception:                              # noqa: BLE001
        pass
    return os.environ.get("MCP_TOKEN") or ""


def build_app():
    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            token = _current_token()               # read per-request → live changes
            if token:
                headers = dict(scope.get("headers") or [])
                if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"www-authenticate", b"Bearer")]})
                    await send({"type": "http.response.body",
                                "body": b'{"error":"unauthorized"}'})
                    return
        await inner(scope, receive, send)
    return app


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8082"))
    if not _current_token():
        print("\n  ⚠  MCP_TOKEN unset — the MCP server is UNAUTHENTICATED. Safe only on\n"
              "     localhost or behind an authenticating reverse proxy.\n")
    print(f"Serving MCP at http://{host}:{port}/mcp")
    uvicorn.run(build_app(), host=host, port=port, log_level="info")
