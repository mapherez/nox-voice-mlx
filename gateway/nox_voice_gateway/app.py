"""Authenticated, same-origin webapp and bounded streaming gateway."""

import asyncio
import hmac
import os
import re
from contextlib import asynccontextmanager, suppress

import anyio
import httpx2 as httpx
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .config import Config, normalize_origin
from .sessions import SESSION_SECONDS, Sessions, cookie_name
from .transport import Controller, ControllerUnavailable


class BodyTooLarge(Exception):
    pass


class BrowserBoundary:
    """Choose a configured browser origin from Host, without trusting forwarded headers."""

    def __init__(self, app, config: Config):
        self.app, self.config = app, config
        self.session_apps = {
            secure: SessionMiddleware(
                app,
                secret_key=config.session_secret,
                session_cookie="__Host-nox_voice_oauth" if secure else "nox_voice_local_oauth",
                max_age=600,
                same_site="lax",
                https_only=secure,
            )
            for secure in (False, True)
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        request = Request(scope) if scope["type"] == "http" else WebSocket(scope, receive, send)
        host = request.headers.get("host", "")
        origin = self.config.origin_for_host(host)
        if origin is None and scope["type"] == "http" and scope["path"] == "/health":
            try:
                # Container health probes use the internal loopback port, not the host port.
                candidate = normalize_origin("http://" + host)
                if candidate.startswith("http://"):
                    origin = candidate
            except ValueError:
                pass
        if origin is None:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse({"error": "Invalid host header."}, status_code=400)(
                scope, receive, send
            )
        scope.setdefault("state", {})["browser_origin"] = origin
        await self.session_apps[origin.startswith("https://")](scope, receive, send)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        total = 0

        async def limited_receive():
            nonlocal total
            event = await receive()
            total += len(event.get("body", b""))
            if total > 32768:
                raise BodyTooLarge
            return event

        try:
            await self.app(scope, limited_receive if scope["type"] == "http" else receive, send)
        except BodyTooLarge:
            await JSONResponse({"error": "Request body is too large."}, status_code=413)(
                scope, receive, send
            )


class Security:
    """Pure ASGI checks preserve streaming and bounded body error propagation."""

    def __init__(self, app, config: Config, sessions: Sessions):
        self.app, self.config, self.sessions = app, config, sessions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        origin = request.state.browser_origin
        secure = origin.startswith("https://")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                websocket_origin = origin.replace("https://", "wss://", 1).replace(
                    "http://", "ws://", 1
                )
                headers = {
                    "cache-control": "no-store",
                    "x-content-type-options": "nosniff",
                    "referrer-policy": "no-referrer",
                    "x-frame-options": "DENY",
                    "permissions-policy": "microphone=(self), camera=(), geolocation=()",
                    "content-security-policy": f"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self' {websocket_origin}; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                }
                if secure:
                    headers["strict-transport-security"] = "max-age=31536000"
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.decode().lower() not in headers
                ]
                message["headers"].extend(
                    (key.encode(), value.encode()) for key, value in headers.items()
                )
            await send(message)

        if request.url.path.startswith("/api/") or request.url.path == "/auth/logout":
            user = self.sessions.get(request.cookies.get(cookie_name(secure)), origin)
            if not user:
                return await JSONResponse({"error": "Sign in to continue."}, status_code=401)(
                    scope, receive, secure_send
                )
            request.state.user = user
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("x-csrf-token", "")
                if request.headers.get("origin") != origin or not hmac.compare_digest(
                    csrf.encode(), user["csrf"].encode()
                ):
                    return await JSONResponse(
                        {"error": "Request verification failed."}, status_code=403
                    )(scope, receive, secure_send)
        await self.app(scope, receive, secure_send)


def create_app(config: Config | None = None, controller: Controller | None = None) -> FastAPI:
    config = config or Config.from_env()
    sessions = Sessions(config.data_dir)
    remote = controller or Controller(config)
    connections: dict[str, set[WebSocket]] = {}
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )

    @asynccontextmanager
    async def lifespan(_):
        yield
        for sockets in list(connections.values()):
            for socket in list(sockets):
                with suppress(RuntimeError):
                    await socket.close(1001, "Webapp shutting down.")
        await remote.close()

    app = FastAPI(
        title="NoX Voice",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    app.state.sessions, app.state.controller, app.state.oauth = sessions, remote, oauth

    app.add_middleware(Security, config=config, sessions=sessions)
    app.add_middleware(BodyLimit)
    app.add_middleware(BrowserBoundary, config=config)

    @app.get("/health")
    async def health():
        return {"status": "ready", "service": "nox-voice-webapp"}

    @app.get("/auth/google/start")
    async def login(request: Request):
        if not config.google_client_id or not config.google_client_secret:
            return JSONResponse({"error": "Google login is not configured."}, status_code=503)
        request.session.clear()
        origin = request.state.browser_origin
        request.session["browser_origin"] = origin
        try:
            return await oauth.google.authorize_redirect(request, origin + "/auth/google/callback")
        except httpx.HTTPError:
            request.session.clear()
            return RedirectResponse("/?login=failed", status_code=303)

    @app.get("/auth/google/callback")
    async def callback(request: Request):
        origin = request.state.browser_origin
        secure = origin.startswith("https://")
        try:
            if request.session.get("browser_origin") != origin:
                raise ValueError("Login origin mismatch.")
            token = await oauth.google.authorize_access_token(request)
            identity = token.get("userinfo", {})
            email = str(identity.get("email", "")).casefold()
            if (
                identity.get("email_verified") is not True
                or email != config.owner_email.casefold()
                or not identity.get("sub")
            ):
                request.session.clear()
                return RedirectResponse("/?login=denied", status_code=303)
            session_token = sessions.create(email, str(identity["sub"]), origin)
            request.session.clear()
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                cookie_name(secure),
                session_token,
                max_age=SESSION_SECONDS,
                secure=secure,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response
        except (OAuthError, ValueError, KeyError, httpx.HTTPError):
            request.session.clear()
            return RedirectResponse("/?login=failed", status_code=303)

    @app.get("/api/session")
    async def identity(request: Request):
        user = request.state.user
        return {
            "email": user["email"],
            "csrfToken": user["csrf"],
            "expiresAt": user["expires"],
            "controllerUrl": config.controller_url,
        }

    @app.post("/auth/logout")
    async def logout(request: Request):
        secure = request.state.browser_origin.startswith("https://")
        token = request.cookies.get(cookie_name(secure))
        sessions.delete(token)
        for socket in list(connections.get(token, set())):
            with suppress(RuntimeError):
                await socket.close(1008, "Signed out.")
        request.session.clear()
        response = JSONResponse({"ok": True})
        response.delete_cookie(cookie_name(secure), secure=secure, httponly=True, samesite="lax")
        return response

    async def forward(method, path, payload=None, timeout=8):
        try:
            status, value = await remote.request(method, path, payload, timeout=timeout)
            if status >= 400:
                return JSONResponse(
                    {
                        "error": "End the conversation before changing settings."
                        if status == 409
                        else "Controller could not complete the request."
                    },
                    status_code=status if status in {400, 409, 422, 503} else 502,
                )
            return JSONResponse(value, status_code=status)
        except ControllerUnavailable as error:
            return JSONResponse({"error": str(error)}, status_code=503)

    @app.get("/api/engine/status")
    async def engine_status():
        try:
            status, value = await remote.request("GET", "/api/engine/status")
            if status != 200:
                raise ControllerUnavailable("Controller rejected the status request.")
            return {**value, "controllerReachable": True}
        except ControllerUnavailable as error:
            return {
                "state": "unreachable",
                "phase": "Controller unavailable",
                "error": str(error),
                "conversationActive": False,
                "memoryBytes": 0,
                "uptimeSeconds": 0,
                "controllerReachable": False,
            }

    @app.post("/api/engine/{action}")
    async def engine_action(action: str):
        if action not in {"start", "stop", "restart"}:
            return JSONResponse({"error": "Unknown engine action."}, status_code=404)
        return await forward("POST", f"/api/engine/{action}", timeout=20)

    @app.get("/api/settings")
    async def get_settings():
        return await forward("GET", "/api/settings")

    @app.put("/api/settings")
    async def update_settings(request: Request):
        try:
            value = await request.json()
        except ValueError:
            return JSONResponse({"error": "Invalid settings."}, status_code=400)
        if (
            not isinstance(value, dict)
            or set(value) != {"voice", "prompt", "bits"}
            or not isinstance(value.get("prompt"), str)
            or len(value["prompt"]) > 4096
        ):
            return JSONResponse({"error": "Invalid settings."}, status_code=422)
        return await forward("PUT", "/api/settings", value, timeout=180)

    async def forward_voice(method: str, path: str, payload=None, timeout=8):
        try:
            status, value = await remote.request(method, path, payload, timeout=timeout)
            if status < 400:
                return JSONResponse(value, status_code=status)
            messages = {
                404: "Model profiles are not available on this controller.",
                409: "Settings changed or the engine is busy. Reload Settings or end the conversation before trying again.",
                422: "Invalid model settings.",
                503: "The selected model is unavailable. Check model preparation and the native controller.",
            }
            return JSONResponse(
                {"error": value["error"] if isinstance(value.get("error"), str) and len(value["error"]) <= 500 else messages.get(status, "Controller could not complete the request.")},
                status_code=status if status in messages else 502,
            )
        except ControllerUnavailable as error:
            return JSONResponse({"error": str(error)}, status_code=503)

    @app.get("/api/voice/v1/providers")
    async def get_providers():
        return await forward_voice("GET", "/api/voice/v1/providers")

    @app.get("/api/voice/v1/settings")
    async def get_profiles():
        return await forward_voice("GET", "/api/voice/v1/settings")

    @app.put("/api/voice/v1/settings")
    async def update_profiles(request: Request):
        try:
            value = await request.json()
        except ValueError:
            return JSONResponse({"error": "Invalid model settings."}, status_code=400)
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "revision", "selectedProvider", "profiles"}
            or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1
            or type(value.get("revision")) is not int
            or value["revision"] < 0
            or not isinstance(value.get("selectedProvider"), str)
            or not isinstance(value.get("profiles"), dict)
        ):
            return JSONResponse({"error": "Invalid model settings."}, status_code=422)
        return await forward_voice("PUT", "/api/voice/v1/settings", value, timeout=180)

    @app.get("/api/voice/v1/engine/status")
    async def get_provider_status():
        return await forward_voice("GET", "/api/voice/v1/engine/status")

    @app.post("/api/voice/v1/engine/{action}")
    async def provider_action(action: str):
        if action not in {"start", "stop", "restart", "load", "eject"}:
            return JSONResponse({"error": "Unknown engine action."}, status_code=404)
        return await forward_voice("POST", f"/api/voice/v1/engine/{action}", timeout=20)

    @app.get("/api/voice/v1/models")
    async def model_library():
        return await forward_voice("GET", "/api/voice/v1/models")

    @app.get("/api/voice/v1/downloads")
    async def downloads():
        return await forward_voice("GET", "/api/voice/v1/downloads")

    @app.delete("/api/voice/v1/downloads")
    async def clear_downloads():
        return await forward_voice("DELETE", "/api/voice/v1/downloads")

    @app.post("/api/voice/v1/downloads")
    async def download(request: Request):
        try:
            value = await request.json()
        except ValueError:
            return JSONResponse({"error": "Invalid download request."}, status_code=422)
        if not isinstance(value, dict) or set(value) != {"provider"} or not isinstance(value["provider"], str) or value["provider"] not in {"personaplex", "lfm3-audio"}:
            return JSONResponse({"error": "Choose a supported model."}, status_code=422)
        return await forward_voice("POST", "/api/voice/v1/downloads", value)

    @app.post("/api/voice/v1/downloads/{download_id}/{action}")
    async def download_action(download_id: str, action: str):
        if not re.fullmatch(r"[a-f0-9]{24}", download_id) or action not in {"cancel", "retry"}:
            return JSONResponse({"error": "Unknown download action."}, status_code=404)
        return await forward_voice("POST", f"/api/voice/v1/downloads/{download_id}/{action}", timeout=15)

    @app.post("/api/voice/v1/providers/{provider_id}/open-folder")
    async def open_model_folder(provider_id: str):
        if provider_id not in {"personaplex", "lfm3-audio"}:
            return JSONResponse({"error": "Unknown model."}, status_code=404)
        return await forward_voice("POST", f"/api/voice/v1/providers/{provider_id}/open-folder")

    @app.get("/api/voice/v1/credentials")
    async def credentials():
        return await forward_voice("GET", "/api/voice/v1/credentials")

    @app.api_route("/api/voice/v1/credentials/{service}", methods=["PUT", "DELETE"])
    async def credential_write(service: str, request: Request):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service):
            return JSONResponse({"error": "Unknown credential service."}, status_code=404)
        value = None
        if request.method == "PUT":
            try:
                value = await request.json()
            except ValueError:
                return JSONResponse({"error": "Enter a valid access token."}, status_code=422)
            if not isinstance(value, dict) or set(value) != {"secret"} or not isinstance(value["secret"], str) or not 8 <= len(value["secret"]) <= 512 or any(char in value["secret"] for char in "\r\n"):
                return JSONResponse({"error": "Enter a valid access token."}, status_code=422)
        return await forward_voice(request.method, f"/api/voice/v1/credentials/{service}", value)

    @app.post("/api/voice/v1/credentials/{service}/test")
    async def credential_test(service: str):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service):
            return JSONResponse({"error": "Unknown credential service."}, status_code=404)
        return await forward_voice("POST", f"/api/voice/v1/credentials/{service}/test", timeout=70)

    @app.post("/api/voice/v1/providers/{provider_id}/prepare")
    async def prepare_provider(provider_id: str):
        if provider_id != "lfm3-audio":
            return JSONResponse({"error": "Unknown model preparation action."}, status_code=404)
        return await forward_voice("POST", "/api/voice/v1/providers/lfm3-audio/prepare")

    @app.get("/api/diagnostics")
    async def diagnostics():
        result = await forward("GET", "/api/diagnostics")
        # Only deployment configuration, never certificates or credentials, is returned.
        import json

        value = json.loads(result.body)
        value["controllerUrl"] = config.controller_url
        return JSONResponse(value, status_code=result.status_code)

    @app.get("/api/events")
    async def events():
        return await forward("GET", "/api/events")

    async def audio_stream(socket: WebSocket, *, versioned: bool):
        origin = socket.state.browser_origin
        token = socket.cookies.get(cookie_name(origin.startswith("https://")))
        if socket.headers.get("origin") != origin or not sessions.get(token, origin):
            await socket.close(1008)
            return
        tasks = []
        try:
            connection = remote.voice_conversation() if versioned else remote.conversation()
            async with connection as upstream:
                await socket.accept()
                connections.setdefault(token, set()).add(socket)

                async def to_controller():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        data = message.get("text") if versioned else message.get("bytes")
                        invalid = (
                            data is None
                            or (versioned and len(data.encode("utf-8")) > 65536)
                            or (not versioned and len(data) != 7680)
                        )
                        if invalid:
                            await socket.close(1003, "Invalid audio frame.")
                            return
                        await upstream.send(data)

                async def to_browser():
                    async for data in upstream:
                        if isinstance(data, bytes):
                            if versioned:
                                await socket.close(1003, "Invalid audio event.")
                                return
                            await socket.send_bytes(data)
                        else:
                            await socket.send_text(data)

                async def session_expiry():
                    while True:
                        await asyncio.sleep(5)
                        if not sessions.get(token, origin):
                            await socket.close(1008, "Session expired. Sign in again.")
                            return

                tasks = [
                    asyncio.create_task(to_controller()),
                    asyncio.create_task(to_browser()),
                    asyncio.create_task(session_expiry()),
                ]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (
            ControllerUnavailable,
            OSError,
            TimeoutError,
            ConnectionClosed,
            InvalidHandshake,
            WebSocketDisconnect,
        ):
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(1011, "Controller connection unavailable.")
        finally:
            for task in tasks:
                task.cancel()
            if token in connections:
                connections[token].discard(socket)
                if not connections[token]:
                    connections.pop(token, None)
            with anyio.move_on_after(2, shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await socket.close(1000)

    @app.websocket("/ws")
    async def conversation(socket: WebSocket):
        await audio_stream(socket, versioned=False)

    @app.websocket("/voice/v1/ws")
    async def versioned_conversation(socket: WebSocket):
        await audio_stream(socket, versioned=True)

    @app.get("/")
    async def index():
        path = config.static_dir / "index.html"
        if not path.is_file():
            return JSONResponse(
                {"error": "Frontend is not built. Run npm ci and npm run build in frontend/."},
                status_code=503,
            )
        return FileResponse(path)

    if (config.static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=config.static_dir / "assets"), name="assets")
    return app


def main():
    import uvicorn

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("NOX_VOICE_PORT", "8080")),
        access_log=False,
        proxy_headers=False,
        ws_max_size=65536,
        ws_max_queue=4,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
