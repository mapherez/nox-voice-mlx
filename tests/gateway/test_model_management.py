import pytest


@pytest.mark.parametrize("path", ["models", "downloads", "credentials"])
def test_model_management_reads_require_authentication(gateway, path):
    _, client, remote = gateway
    assert client.get("/api/voice/v1/" + path).status_code == 401
    assert not remote.calls


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/voice/v1/downloads", {"provider": "personaplex"}),
    ("post", "/api/voice/v1/providers/personaplex/open-folder", None),
    ("put", "/api/voice/v1/credentials/huggingface", {"secret": "hf_fixtureToken123"}),
    ("delete", "/api/voice/v1/credentials/huggingface", None),
    ("post", "/api/voice/v1/credentials/huggingface/test", None),
    ("post", "/api/voice/v1/engine/load", None),
    ("post", "/api/voice/v1/engine/eject", None),
])
def test_management_mutations_require_csrf_and_use_fixed_controller_routes(authenticated, method, path, body):
    _, client, remote, _, headers = authenticated
    args = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **args).status_code == 403
    assert not remote.calls
    assert getattr(client, method)(path, headers=headers, **args).status_code == 200
    assert remote.calls == [(method.upper(), path, body)]


@pytest.mark.parametrize("body", [{"provider": []}, {"provider": "unknown"}, {"provider": "lfm3-audio", "url": "https://example.invalid"}, {}])
def test_download_is_not_an_arbitrary_url_or_path_fetcher(authenticated, body):
    _, client, remote, _, headers = authenticated
    assert client.post("/api/voice/v1/downloads", headers=headers, json=body).status_code == 422
    assert not remote.calls


def test_bad_credential_requests_never_echo_secret(authenticated):
    _, client, remote, _, headers = authenticated
    secret = "hf_privateFixtureToken123"
    result = client.put("/api/voice/v1/credentials/huggingface", headers=headers, json={"secret": secret, "extra": True})
    assert result.status_code == 422 and secret not in result.text
    assert not remote.calls
