"""A random per-process capability protects local business APIs from web pages."""
from __future__ import annotations

import secrets
from contextlib import nullcontext
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse


def register_security(app):
    token = secrets.token_urlsafe(32)
    app.state.local_token = token
    from .workspaces import rotate_local_capability
    store = app.state.store
    def account_changed():
        collaboration = getattr(app.state, "canvas_collaboration", None)
        if collaboration:
            collaboration.reset_idle_binding()
        rotate_local_capability(app)
    store.on_change = account_changed

    @app.middleware("http")
    async def local_api_access(request: Request, call_next):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"detail": "工作台仅允许回环主机访问"}, status_code=400)
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
                    return JSONResponse({"detail": "只接受本机工作台请求"}, status_code=403)
            if request.url.path not in {"/api/bootstrap", "/api/health"}:
                supplied = request.headers.get("x-workbench-token", "")
                # Files need a URL capability because <img> and downloads cannot set headers.
                if request.method == "GET" and (request.url.path.startswith("/api/files/") or request.url.path.endswith("/file")):
                    supplied = supplied or request.query_params.get("token", "")
                if not secrets.compare_digest(supplied, app.state.local_token):
                    return JSONResponse({"detail": "工作台连接已失效，请重新打开客户端"}, status_code=401,
                                        headers={"X-Workbench-Account-Scope": getattr(store, "account_scope", "anonymous")})
        with store.bind() if hasattr(store, "bind") else nullcontext():
            request_scope = getattr(store, "bound_scope", "anonymous")
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            if "X-Workbench-Account-Scope" not in response.headers:
                response.headers["X-Workbench-Account-Scope"] = request_scope
        return response

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "same-site", "none"}:
            return JSONResponse({"detail": "请从本机工作台打开"}, status_code=403)
        return {"token": app.state.local_token, "product": "architecture-workbench", "account_scope": getattr(store, "account_scope", "anonymous")}
