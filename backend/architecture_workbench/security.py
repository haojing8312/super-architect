"""A random per-process capability protects local business APIs from web pages."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

LOOPBACK = {"localhost", "127.0.0.1", "::1", "testserver"}


def register_security(app):
    token = secrets.token_urlsafe(32)
    app.state.local_token = token

    @app.middleware("http")
    async def local_api_access(request: Request, call_next):
        # The service listens on loopback only; reject rebound or proxied hosts
        # before any business handler sees the request.
        if request.url.hostname not in LOOPBACK:
            return JSONResponse({"detail": "工作台仅允许回环主机访问"}, status_code=400)
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.hostname not in LOOPBACK:
                    return JSONResponse({"detail": "只接受本机工作台请求"}, status_code=403)
            if request.url.path not in {"/api/bootstrap", "/api/health"}:
                supplied = request.headers.get("x-workbench-token", "")
                # Files need a URL capability because <img> and downloads cannot set headers.
                if request.method == "GET" and (request.url.path.startswith("/api/files/") or request.url.path.endswith("/file")):
                    supplied = supplied or request.query_params.get("token", "")
                if not secrets.compare_digest(supplied, app.state.local_token):
                    return JSONResponse({"detail": "工作台连接已失效，请重新打开客户端"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "same-site", "none"}:
            return JSONResponse({"detail": "请从本机工作台打开"}, status_code=403)
        return {"token": app.state.local_token, "product": "architecture-workbench", "mode": "local"}
