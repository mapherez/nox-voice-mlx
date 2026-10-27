"""Report local TLS setup failures before trying to connect to the Mac."""

import ssl
from dataclasses import replace
from pathlib import Path

import pytest
from controller.certificates import create_certificates
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app
from nox_voice_gateway.sessions import COOKIE
from nox_voice_gateway.transport import Controller, ControllerUnavailable


@pytest.fixture
def tls_config(config, tmp_path):
    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    return replace(
        config,
        ca_file=bundle / "ca.crt",
        cert_file=bundle / "client.crt",
        key_file=bundle / "client.key",
    )


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("ca_file", "Controller CA certificate"),
        ("cert_file", "Controller client certificate"),
        ("key_file", "Controller client private key"),
    ],
)
def test_missing_certificate_identifies_mount(tls_config, field, label):
    path = getattr(tls_config, field)
    path.unlink()
    with pytest.raises(ControllerUnavailable, match=f"{label} is missing") as error:
        Controller(tls_config).context()
    assert str(path) in str(error.value)


@pytest.mark.parametrize("field", ["ca_file", "cert_file", "key_file"])
def test_unreadable_certificate_reports_permissions(tls_config, monkeypatch, field):
    original = Path.open
    path = getattr(tls_config, field)

    def deny(self, *args, **kwargs):
        if self == path:
            raise PermissionError("sensitive OS details must not be returned")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(ControllerUnavailable, match="permission denied") as error:
        Controller(tls_config).context()
    assert str(path) in str(error.value)
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("field", ["ca_file", "cert_file", "key_file"])
def test_invalid_pem_does_not_expose_contents(tls_config, field):
    getattr(tls_config, field).write_text("invalid-secret-content")
    with pytest.raises(ControllerUnavailable, match="invalid") as error:
        Controller(tls_config).context()
    assert "invalid-secret-content" not in str(error.value)


def test_mismatched_client_key_is_identified(tls_config, tmp_path):
    other = create_certificates(tmp_path / "other", "127.0.0.1")
    remote = Controller(replace(tls_config, key_file=other / "client.key"))
    with pytest.raises(ControllerUnavailable, match="do not match"):
        remote.context()


def test_encrypted_key_does_not_prompt(tls_config):
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(tls_config.key_file.read_bytes(), password=None)
    tls_config.key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"test-only-password"),
        )
    )
    with pytest.raises(ControllerUnavailable, match="private key is invalid"):
        Controller(tls_config).context()


def test_failed_load_can_retry_and_valid_context_keeps_verification(tls_config):
    remote = Controller(tls_config)
    contents = tls_config.ca_file.read_bytes()
    tls_config.ca_file.unlink()
    with pytest.raises(ControllerUnavailable):
        remote.context()
    tls_config.ca_file.write_bytes(contents)
    context = remote.context()
    assert remote.context() is context
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_settings_and_diagnostics_return_specific_error(config):
    app = create_app(config)
    with TestClient(app, base_url=config.public_url) as client:
        token = app.state.sessions.create(config.owner_email, "test", config.public_url)
        client.cookies.set(COOKIE, token, domain="voice.test", path="/")
        for endpoint in ("/api/settings", "/api/diagnostics"):
            response = client.get(endpoint)
            assert response.status_code == 503
            assert "Controller CA certificate is missing" in response.json()["error"]
