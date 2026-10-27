"""HTTP loopback keeps the real login, CSRF and streaming authorization flow."""

from dataclasses import replace

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app
from nox_voice_gateway.sessions import COOKIE, cookie_name
from starlette.websockets import WebSocketDisconnect
from test_streaming import install_stream


@pytest.mark.parametrize(
    "origin", ["http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080"]
)
def test_loopback_http_is_allowed_without_weakening_controller_tls(config, origin):
    local = replace(config, public_url=origin)
    assert origin in local.browser_origins
    with pytest.raises(ValueError):
        replace(local, controller_url="http://127.0.0.1:8443")


@pytest.mark.parametrize(
    "origin",
    [
        "http://192.168.1.10:8080",
        "http://voice.example.com",
        "http://0.0.0.0:8080",
        "http://localhost.attacker.test:8080",
        "http://127.0.0.1.attacker.test:8080",
        "http://user:secret@localhost:8080",
        "http://localhost:8080/path",
    ],
)
def test_plain_http_is_restricted_to_exact_loopback_hosts(config, origin):
    with pytest.raises(ValueError):
        replace(config, public_url=origin)


@pytest.fixture
def local_gateway(config, gateway):
    _, _, remote = gateway
    local = replace(config, public_url="http://localhost:8080", data_dir=config.data_dir / "local")
    app = create_app(local, remote)
    with TestClient(app, base_url=local.public_url) as client:
        yield app, client, remote


def test_local_google_login_cookie_csrf_stream_and_logout(local_gateway, monkeypatch):
    app, client, remote = local_gateway
    assert client.get("/api/session").status_code == 401

    async def authorize(request, redirect_uri):
        assert redirect_uri == "http://localhost:8080/auth/google/callback"
        request.session["test_state"] = "pending"
        return RedirectResponse("https://accounts.google.com/test-login")

    async def identity(request):
        assert request.session["test_state"] == "pending"
        return {"userinfo": {"email": "owner@example.test", "email_verified": True, "sub": "id"}}

    monkeypatch.setattr(app.state.oauth.google, "authorize_redirect", authorize)
    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", identity)
    start = client.get("/auth/google/start", follow_redirects=False)
    oauth_cookie = start.headers["set-cookie"]
    assert "nox_voice_local_oauth=" in oauth_cookie
    assert "httponly" in oauth_cookie.lower() and "secure" not in oauth_cookie.lower()
    response = client.get("/auth/google/callback", follow_redirects=False)
    session_cookie = cookie_name(False)
    token = response.cookies[session_cookie]
    assert COOKIE not in response.cookies
    header = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(session_cookie + "=")
    )
    assert "HttpOnly" in header and "SameSite=lax" in header and "Secure" not in header
    session = client.get("/api/session")
    assert session.status_code == 200
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": session.json()["csrfToken"]}
    assert client.post("/api/engine/start").status_code == 403
    assert (
        client.post(
            "/api/engine/start", headers={**headers, "Origin": "http://attacker.test"}
        ).status_code
        == 403
    )
    assert client.post("/api/engine/start", headers=headers).status_code == 200

    install_stream(remote)
    ws_headers = {"Origin": headers["Origin"], "Cookie": f"{session_cookie}={token}"}
    with client.websocket_connect("ws://localhost:8080/ws", headers=ws_headers) as socket:
        pcm = b"\0" * 7680
        socket.send_bytes(pcm)
        assert socket.receive_bytes() == pcm
        assert client.post("/auth/logout", headers=headers).status_code == 200
        assert socket.receive()["type"] == "websocket.close"
    assert app.state.sessions.get(token) is None
    assert client.get("/api/session").status_code == 401


def test_localhost_keeps_origin_and_host_checks(local_gateway):
    app, client, _ = local_gateway
    token = app.state.sessions.create("owner@example.test", "id", "http://localhost:8080")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "ws://localhost:8080/ws",
            headers={
                "Origin": "http://attacker.test",
                "Cookie": f"{cookie_name(False)}={token}",
            },
        ):
            pass
    assert client.get("/health", headers={"Host": "attacker.test"}).status_code == 400
    health = client.get("/health")
    assert "strict-transport-security" not in health.headers
    assert "ws://localhost:8080" in health.headers["content-security-policy"]


def test_https_production_cookie_policy_is_preserved(gateway):
    _, client, _ = gateway
    assert cookie_name(True) == COOKIE
    health = client.get("/health")
    assert "strict-transport-security" in health.headers
    assert "wss://voice.test" in health.headers["content-security-policy"]
