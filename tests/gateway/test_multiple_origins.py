"""The same gateway serves local and HTTPS browser origins concurrently."""

import sqlite3
import time
from dataclasses import replace

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app
from nox_voice_gateway.config import Config
from nox_voice_gateway.sessions import COOKIE, Sessions, cookie_name, token_digest
from starlette.websockets import WebSocketDisconnect
from test_streaming import install_stream

LOCAL = "http://localhost:8090"
PUBLIC = "https://voice.test"


@pytest.fixture
def multiple_origins(config, gateway, monkeypatch):
    _, _, remote = gateway
    settings = replace(config, web_port=8090, data_dir=config.data_dir / "multiple")
    app = create_app(settings, remote)
    redirects = []

    async def authorize(request, redirect_uri):
        redirects.append(redirect_uri)
        request.session["provider_state"] = "signed-state"
        return RedirectResponse("https://accounts.google.com/test-login")

    async def identity(request):
        assert request.session["provider_state"] == "signed-state"
        return {"userinfo": {"email": settings.owner_email, "email_verified": True, "sub": "id"}}

    monkeypatch.setattr(app.state.oauth.google, "authorize_redirect", authorize)
    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", identity)

    async def proxy_transport(scope, receive, send):
        # Browser-facing HTTPS terminates at the proxy. The gateway sees HTTP/WS.
        scope = dict(scope)
        if scope["type"] == "http":
            scope["scheme"] = "http"
        elif scope["type"] == "websocket":
            scope["scheme"] = "ws"
        await app(scope, receive, send)

    with TestClient(proxy_transport, base_url=PUBLIC) as client:
        yield app, client, remote, redirects


def sign_in(client, origin):
    assert client.get(origin + "/auth/google/start", follow_redirects=False).status_code == 307
    response = client.get(origin + "/auth/google/callback", follow_redirects=False)
    assert response.headers["location"] == "/"
    return response


def test_local_and_public_remain_usable_in_one_configuration(multiple_origins):
    _, client, remote, redirects = multiple_origins
    install_stream(remote)
    for origin in (LOCAL, PUBLIC):
        response = sign_in(client, origin)
        secure = origin.startswith("https://")
        name = cookie_name(secure)
        assert name in response.cookies
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(name + "=")
        )
        assert ("Secure" in cookie) is secure
        assert "HttpOnly" in cookie
    assert redirects == [LOCAL + "/auth/google/callback", PUBLIC + "/auth/google/callback"]

    for origin in (LOCAL, PUBLIC):
        session = client.get(origin + "/api/session")
        assert session.status_code == 200
        headers = {"Origin": origin, "X-CSRF-Token": session.json()["csrfToken"]}
        assert client.post(origin + "/api/engine/start", headers=headers).status_code == 200
        other = PUBLIC if origin == LOCAL else LOCAL
        assert (
            client.post(
                origin + "/api/engine/start", headers={**headers, "Origin": other}
            ).status_code
            == 403
        )
        with client.websocket_connect(
            origin.replace("https://", "wss://").replace("http://", "ws://") + "/ws",
            headers={"Origin": origin},
        ) as socket:
            pcm = b"\0" * 7680
            socket.send_bytes(pcm)
            assert socket.receive_bytes() == pcm

    local_session = client.get(LOCAL + "/api/session").json()
    assert (
        client.post(
            LOCAL + "/auth/logout",
            headers={"Origin": LOCAL, "X-CSRF-Token": local_session["csrfToken"]},
        ).status_code
        == 200
    )
    assert client.get(LOCAL + "/api/session").status_code == 401
    assert client.get(PUBLIC + "/api/session").status_code == 200


def test_sessions_cannot_be_replayed_on_another_allowed_origin(multiple_origins):
    _, client, _, _ = multiple_origins
    for source, target in ((LOCAL, PUBLIC), (PUBLIC, LOCAL)):
        response = sign_in(client, source)
        token = response.cookies[cookie_name(source.startswith("https://"))]
        headers = {"Cookie": f"{cookie_name(target.startswith('https://'))}={token}"}
        assert client.get(target + "/api/session", headers=headers).status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                target.replace("https://", "wss://").replace("http://", "ws://") + "/ws",
                headers={**headers, "Origin": target},
            ):
                pass


def test_oauth_state_cannot_be_moved_between_origins(multiple_origins):
    _, client, _, _ = multiple_origins
    response = client.get(LOCAL + "/auth/google/start", follow_redirects=False)
    state = response.cookies["nox_voice_local_oauth"]
    callback = client.get(
        PUBLIC + "/auth/google/callback",
        headers={"Cookie": f"__Host-nox_voice_oauth={state}"},
        follow_redirects=False,
    )
    assert callback.headers["location"] == "/?login=failed"
    assert COOKIE not in callback.cookies


def test_forwarded_headers_do_not_override_an_allowed_host(multiple_origins):
    _, client, _, redirects = multiple_origins
    forged = {
        "X-Forwarded-Host": "attacker.test",
        "X-Forwarded-Proto": "https",
        "Forwarded": "host=attacker.test;proto=https",
    }
    assert (
        client.get(LOCAL + "/auth/google/start", headers=forged, follow_redirects=False).status_code
        == 307
    )
    assert redirects[-1] == LOCAL + "/auth/google/callback"
    assert (
        client.get(
            "http://attacker.test/health", headers={"X-Forwarded-Host": "voice.test"}
        ).status_code
        == 400
    )
    assert client.get("http://voice.test:8090/health").status_code == 400
    assert client.get("http://127.0.0.1:8080/health").status_code == 200
    assert client.get("http://127.0.0.1:8080/api/session").status_code == 400


def test_headers_and_cookies_follow_each_browser_origin(multiple_origins):
    _, client, _, _ = multiple_origins
    for origin in (LOCAL, PUBLIC):
        response = client.get(origin + "/auth/google/start", follow_redirects=False)
        secure = origin == PUBLIC
        cookie = response.headers["set-cookie"]
        assert ("secure" in cookie.lower()) is secure
        assert ("strict-transport-security" in response.headers) is secure
        assert (
            origin.replace("https://", "wss://").replace("http://", "ws://")
            in response.headers["content-security-policy"]
        )


def test_config_keeps_local_origins_and_normalizes_the_public_domain(config):
    local = replace(config, public_url="", web_port=8090)
    dual = replace(
        local,
        public_url="https://VOICE.test:443/",
        additional_origins=("https://voice.internal.test",),
    )
    assert set(local.browser_origins) <= set(dual.browser_origins)
    assert LOCAL in dual.browser_origins
    assert dual.origin_for_host("voice.test") == PUBLIC
    assert dual.origin_for_host("VOICE.test:443") == PUBLIC
    assert dual.origin_for_host("voice.internal.test") == "https://voice.internal.test"
    assert dual.origin_for_host("voice.test.attacker.test") is None


def test_environment_derives_local_url_from_one_port_without_replacing_it(monkeypatch):
    for name in ("NOX_VOICE_GOOGLE_CLIENT_SECRET_FILE", "NOX_VOICE_SESSION_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NOX_VOICE_OWNER_EMAIL", "owner@example.test")
    monkeypatch.setenv("NOX_VOICE_GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("NOX_VOICE_SESSION_SECRET", "test-signing-secret-that-is-long-enough")
    monkeypatch.setenv("NOX_VOICE_CONTROLLER_URL", "https://127.0.0.1:8443")
    monkeypatch.setenv("NOX_VOICE_WEB_PORT", "8090")
    monkeypatch.setenv("NOX_VOICE_PUBLIC_URL", PUBLIC)
    monkeypatch.setenv("NOX_VOICE_ALLOWED_ORIGINS", "https://voice.internal.test")
    config = Config.from_env()
    assert LOCAL in config.browser_origins
    assert PUBLIC in config.browser_origins
    assert "https://voice.internal.test" in config.browser_origins


def test_only_configured_origins_and_valid_ports_are_allowed(config):
    for kwargs in (
        {"web_port": 0},
        {"web_port": 65536},
        {"additional_origins": ("http://192.168.1.50:8080",)},
        {"additional_origins": ("https://*.example.com",)},
        {"public_url": "https://localhost:8080"},
    ):
        with pytest.raises(ValueError):
            replace(config, **kwargs)


def test_existing_session_database_is_migrated_and_requires_a_new_origin_bound_login(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    token = "legacy-test-session"
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL)"
        )
        database.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (token_digest(token), "owner@example.test", "id", "csrf", time.time() + 3600),
        )
    sessions = Sessions(tmp_path)
    assert sessions.get(token, PUBLIC) is None
    new = sessions.create("owner@example.test", "id", PUBLIC)
    assert sessions.get(new, PUBLIC) is not None
    assert sessions.get(new, LOCAL) is None
