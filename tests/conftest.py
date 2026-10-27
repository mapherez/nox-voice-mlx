import pytest
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app
from nox_voice_gateway.config import Config
from nox_voice_gateway.sessions import COOKIE


class FakeController:
    def __init__(self):
        self.calls = []
        self.available = True

    async def request(self, method, path, payload=None, timeout=8):
        from nox_voice_gateway.transport import ControllerUnavailable

        if not self.available:
            raise ControllerUnavailable("Mac controller is unavailable.")
        self.calls.append((method, path, payload))
        if path == "/api/engine/status":
            return 200, {
                "state": "stopped",
                "phase": "Stopped",
                "error": None,
                "conversationActive": False,
                "memoryBytes": 0,
            }
        return 200, {"ok": True}

    async def close(self):
        pass


@pytest.fixture
def config(tmp_path):
    return Config(
        public_url="https://voice.test",
        owner_email="owner@example.test",
        google_client_id="test-client",
        google_client_secret="test-secret",
        session_secret="a-test-signing-key-at-least-thirty-two-characters",
        controller_url="https://127.0.0.1:8443",
        ca_file=tmp_path / "ca.crt",
        cert_file=tmp_path / "client.crt",
        key_file=tmp_path / "client.key",
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
    )


@pytest.fixture
def gateway(config):
    remote = FakeController()
    app = create_app(config, remote)
    with TestClient(app, base_url=config.public_url) as client:
        yield app, client, remote


@pytest.fixture
def authenticated(gateway):
    app, client, remote = gateway
    token = app.state.sessions.create("owner@example.test", "google-subject")
    client.cookies.set(COOKIE, token, domain="voice.test", path="/")
    user = app.state.sessions.get(token)
    headers = {"Origin": "https://voice.test", "X-CSRF-Token": user["csrf"]}
    return app, client, remote, token, headers
