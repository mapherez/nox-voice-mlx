import json
import time
from dataclasses import replace

import httpx2 as httpx
import pytest
from nox_voice_gateway.sessions import COOKIE
from starlette.websockets import WebSocketDisconnect


def test_apis_require_a_session(gateway):
    _, client, remote = gateway
    for path in [
        "/api/session",
        "/api/settings",
        "/api/engine/status",
        "/api/events",
        "/api/diagnostics",
    ]:
        assert client.get(path).status_code == 401
    assert client.post("/api/engine/start").status_code == 401
    assert not remote.calls
    assert client.get("/health").json() == {"status": "ready", "service": "nox-voice-webapp"}


def test_mutations_require_both_origin_and_csrf(authenticated):
    _, client, remote, _, headers = authenticated
    assert client.post("/api/engine/start").status_code == 403
    assert (
        client.post(
            "/api/engine/start", headers={**headers, "Origin": "https://attacker.test"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/engine/start", headers={"Origin": headers["Origin"]}).status_code == 403
    )
    assert client.post("/api/engine/start", headers=headers).status_code == 200
    assert remote.calls == [("POST", "/api/engine/start", None)]


def test_logout_revokes_the_server_session(authenticated):
    app, client, _, token, headers = authenticated
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert app.state.sessions.get(token) is None
    assert client.get("/api/session").status_code == 401
    with app.state.sessions.connection() as database:
        assert not database.execute("SELECT * FROM sessions").fetchall()


def test_expired_sessions_are_rejected(authenticated):
    app, client, _, _, _ = authenticated
    with app.state.sessions.connection() as database:
        database.execute("UPDATE sessions SET expires = ?", (time.time() - 1,))
    assert client.get("/api/session").status_code == 401


def test_session_token_and_secrets_are_not_exposed(authenticated):
    app, client, _, token, _ = authenticated
    response = client.get("/api/session")
    assert response.json()["email"] == "owner@example.test"
    assert token not in response.text
    with app.state.sessions.connection() as database:
        assert token not in json.dumps(dict(database.execute("SELECT * FROM sessions").fetchone()))


@pytest.mark.parametrize(
    "verified,email,subject",
    [
        (False, "owner@example.test", "id"),
        (True, "other@example.test", "id"),
        (True, "owner@example.test", ""),
    ],
)
def test_google_identity_must_be_verified_and_allowed(
    gateway, monkeypatch, verified, email, subject
):
    app, client, _ = gateway

    async def identity(_):
        return {"userinfo": {"email": email, "email_verified": verified, "sub": subject}}

    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", identity)
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert response.headers["location"] == "/?login=denied"
    assert COOKIE not in response.cookies


def test_successful_google_login_sets_secure_opaque_cookie(gateway, monkeypatch):
    app, client, _ = gateway

    async def identity(_):
        return {
            "userinfo": {"email": "OWNER@example.test", "email_verified": True, "sub": "google-id"}
        }

    monkeypatch.setattr(app.state.oauth.google, "authorize_access_token", identity)
    response = client.get("/auth/google/callback", follow_redirects=False)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/session").json()["email"] == "owner@example.test"


def test_unknown_oauth_state_cannot_authenticate(gateway):
    _, client, _ = gateway
    response = client.get("/auth/google/callback?code=fake&state=unknown", follow_redirects=False)
    assert response.headers["location"] == "/?login=failed"
    assert COOKIE not in response.cookies


@pytest.mark.parametrize(
    "route,method", [("start", "authorize_redirect"), ("callback", "authorize_access_token")]
)
def test_google_network_failure_does_not_expose_details(gateway, monkeypatch, route, method):
    app, client, _ = gateway

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("Sensitive upstream diagnostics")

    monkeypatch.setattr(app.state.oauth.google, method, unavailable)
    response = client.get(f"/auth/google/{route}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?login=failed"
    assert COOKIE not in response.cookies
    assert "Sensitive" not in response.text


def test_websocket_rejects_missing_cookie_and_cross_origin(authenticated):
    _, client, _, token, _ = authenticated
    for headers in [
        {"Origin": "https://attacker.test", "Cookie": f"{COOKIE}={token}"},
        {"Origin": "https://voice.test", "Cookie": ""},
    ]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("wss://voice.test/ws", headers=headers):
                pass


def test_engine_destination_cannot_be_overridden_by_query(authenticated):
    _, client, remote, _, _ = authenticated
    assert client.get("/api/engine/status?worker_addr=attacker.test").status_code == 200
    assert remote.calls == [("GET", "/api/engine/status", None)]


def test_public_config_rejects_insecure_urls_and_weak_secret(config):
    for kwargs in [
        {"controller_url": "http://127.0.0.1:8443"},
        {"public_url": "https://user:secret@voice.test"},
        {"session_secret": "weak"},
        {"owner_email": ""},
    ]:
        with pytest.raises(ValueError):
            replace(config, **kwargs)


def test_controller_offline_keeps_panel_apis_available(authenticated):
    _, client, remote, _, headers = authenticated
    remote.available = False
    response = client.get("/api/engine/status")
    assert response.status_code == 200
    assert response.json()["controllerReachable"] is False
    assert client.get("/api/session").status_code == 200
    assert client.post("/api/engine/start", headers=headers).status_code == 503


def test_payload_limits_and_invalid_settings(authenticated):
    _, client, remote, _, headers = authenticated
    assert (
        client.put(
            "/api/settings",
            headers=headers,
            json={"voice": "NATF0", "prompt": "a" * 4097, "bits": 4},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            headers={**headers, "Content-Type": "application/json"},
            content=b"x" * 40000,
        ).status_code
        == 413
    )
    assert not remote.calls


def test_browser_security_headers_and_no_external_assets(gateway):
    _, client, _ = gateway
    response = client.get("/health")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["permissions-policy"] == "microphone=(self), camera=(), geolocation=()"
