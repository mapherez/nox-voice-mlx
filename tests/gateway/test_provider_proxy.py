import pytest


def document():
    return {
        "schemaVersion": 1,
        "revision": 0,
        "selectedProvider": "personaplex",
        "profiles": {
            "personaplex": {"voice": "NATF0", "prompt": "A persona", "bits": 4},
            "lfm3-audio": {"voice": "male"},
        },
    }


@pytest.mark.parametrize("path", ["providers", "settings", "engine/status"])
def test_profile_reads_require_authentication(gateway, path):
    _, client, remote = gateway
    assert client.get("/api/voice/v1/" + path).status_code == 401
    assert not remote.calls


def test_profiles_share_origin_csrf_and_fixed_controller_transport(authenticated):
    _, client, remote, _, headers = authenticated
    path, value = "/api/voice/v1/settings", document()
    assert client.put(path, json=value).status_code == 403
    assert client.put(path, json=value, headers={"Origin": headers["Origin"]}).status_code == 403
    assert not remote.calls
    assert client.put(path, json=value, headers=headers).status_code == 200
    assert remote.calls == [("PUT", path, value)]


@pytest.mark.parametrize(
    "value", [{}, {**document(), "revision": True}, {**document(), "schemaVersion": 2}]
)
def test_malformed_profile_envelope_never_reaches_controller(authenticated, value):
    _, client, remote, _, headers = authenticated
    assert client.put("/api/voice/v1/settings", json=value, headers=headers).status_code == 422
    assert not remote.calls


def test_versioned_errors_are_safe_and_unknown_actions_are_not_forwarded(authenticated):
    _, client, remote, _, headers = authenticated

    async def conflict(*args, **kwargs):
        return 409, {"detail": "do not leak this private persona"}

    remote.request = conflict
    response = client.put("/api/voice/v1/settings", json=document(), headers=headers)
    assert response.status_code == 409
    assert "private persona" not in response.text
    assert "Reload" in response.json()["error"]
    assert client.post("/api/voice/v1/engine/arbitrary", headers=headers).status_code == 404


def test_old_controller_404_is_preserved_for_frontend_compatibility(authenticated):
    _, client, remote, _, _ = authenticated

    async def old(*args, **kwargs):
        return 404, {"detail": "Not Found"}

    remote.request = old
    assert client.get("/api/voice/v1/providers").status_code == 404


def test_model_preparation_is_explicit_authenticated_and_uses_only_fixed_paths(authenticated):
    _, client, remote, _, headers = authenticated
    path = "/api/voice/v1/providers/lfm3-audio/prepare"
    assert client.post(path).status_code == 403
    assert not remote.calls
    assert (
        client.post("/api/voice/v1/providers/unknown/prepare", headers=headers).status_code == 404
    )
    assert client.post(path, headers=headers).status_code == 200
    assert remote.calls == [("POST", path, None)]
