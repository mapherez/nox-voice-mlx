import asyncio
import json
from dataclasses import replace

import httpx2 as httpx
import pytest
from controller.config import ControllerConfig, VoiceSettings
from controller.profiles import VoiceConfiguration, load_profiles
from controller.server import create_app
from controller.supervisor import Supervisor
from fastapi import HTTPException
from fastapi.testclient import TestClient


def configuration(tmp_path):
    return ControllerConfig(data_dir=tmp_path, weights_dir=tmp_path / "weights")


def test_legacy_read_and_catalogue_do_not_migrate_or_load_models(tmp_path):
    config = configuration(tmp_path)
    legacy = b'{"voice":"NATM2","prompt":"Keep this persona","bits":8}\n'
    config.settings_file.write_bytes(legacy)
    with TestClient(create_app(config)) as client:
        assert client.get("/api/settings").json() == json.loads(legacy)
        saved = client.get("/api/voice/v1/settings").json()
        assert saved["schemaVersion"] == 1 and saved["revision"] == 0
        assert saved["selectedProvider"] == "personaplex"
        assert saved["profiles"]["personaplex"] == json.loads(legacy)
        catalogue = client.get("/api/voice/v1/providers").json()
        plex, lfm = catalogue["providers"]
        assert plex["capabilities"]["legacyAudio"] is True
        assert plex["settingsSchema"]["properties"]["bits"]["enum"] == [0, 4, 5, 6, 8]
        assert len(plex["settingsSchema"]["properties"]["voice"]["enum"]) == 18
        assert lfm["implemented"] and lfm["selectable"]
        assert lfm["availability"] == "preparation_required"
        assert lfm["capabilities"]["audioChecks"] and not lfm["capabilities"]["executor"]
        assert lfm["audio"]["inputRate"] == 16000
        assert "Portuguese" in lfm["settingsSchema"]["properties"]["inputLanguage"]["enum"]
        assert client.get("/api/engine/status").json()["state"] == "stopped"
    assert config.settings_file.read_bytes() == legacy
    assert not config.legacy_settings_backup.exists()
    assert not config.worker_settings_file.exists()


def test_explicit_save_migrates_with_private_backup_and_rejects_stale_revision(tmp_path):
    config = configuration(tmp_path)
    legacy = b'{"voice":"NATF1","prompt":"Original persona","bits":6}\n'
    config.settings_file.write_bytes(legacy)
    with TestClient(create_app(config)) as client:
        saved = client.get("/api/voice/v1/settings").json()
        saved["profiles"]["personaplex"]["prompt"] = "New persona"
        first = client.put("/api/voice/v1/settings", json=saved)
        assert first.status_code == 200
        assert first.json()["revision"] == 1
        assert first.json()["profiles"]["personaplex"]["voice"] == "NATF1"
        assert client.get("/api/engine/status").json()["state"] == "stopped"
        assert client.put("/api/voice/v1/settings", json=saved).status_code == 409
        assert config.load_settings().prompt == "New persona"
    assert config.legacy_settings_backup.read_bytes() == legacy
    assert config.legacy_settings_backup.stat().st_mode & 0o077 == 0
    assert config.settings_file.stat().st_mode & 0o077 == 0
    assert not config.worker_settings_file.exists()


def test_legacy_write_preserves_inactive_provider_and_versioned_revision(tmp_path):
    config = configuration(tmp_path)
    with TestClient(create_app(config)) as client:
        saved = client.get("/api/voice/v1/settings").json()
        saved["profiles"]["lfm3-audio"]["voice"] = "male"
        assert client.put("/api/voice/v1/settings", json=saved).status_code == 200
        legacy = {"voice": "VARM2", "prompt": "Legacy client persona", "bits": 5}
        assert client.put("/api/settings", json=legacy).json() == legacy
        stored = client.get("/api/voice/v1/settings").json()
        assert stored["profiles"]["lfm3-audio"]["voice"] == "male"
        assert stored["revision"] == 2
        assert stored["selectedProvider"] == "personaplex"
        assert client.put("/api/voice/v1/settings", json=stored).json()["revision"] == 2
    assert config.load_settings() == VoiceSettings(**legacy)


def test_unimplemented_selection_does_not_stop_or_start_a_worker(tmp_path, monkeypatch):
    import asyncio

    from controller.providers import PROVIDERS

    monkeypatch.setitem(
        PROVIDERS, "lfm3-audio", replace(PROVIDERS["lfm3-audio"], worker_module=None)
    )

    manager = Supervisor(configuration(tmp_path))
    manager.state = "ready"
    manager.active = True
    calls = []

    async def spawn(*args, **kwargs):
        calls.append("spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with TestClient(create_app(manager.config, manager)) as client:
        saved = client.get("/api/voice/v1/settings").json()
        saved["selectedProvider"] = "lfm3-audio"
        assert client.put("/api/voice/v1/settings", json=saved).status_code == 503
        assert manager.active and manager.state == "ready"
        assert manager.configuration.selectedProvider == "personaplex"
        assert calls == []
        assert not manager.config.settings_file.exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(schemaVersion=2),
        lambda data: data.update(revision=True),
        lambda data: data.pop("revision"),
        lambda data: data["profiles"].pop("lfm3-audio"),
        lambda data: data.update(selectedProvider="arbitrary-model.py"),
        lambda data: data["profiles"]["personaplex"].update(voice="../../file"),
        lambda data: data["profiles"]["lfm3-audio"].update(unknown=True),
    ],
)
def test_invalid_api_profile_is_not_saved(tmp_path, change):
    config = configuration(tmp_path)
    with TestClient(create_app(config)) as client:
        saved = client.get("/api/voice/v1/settings").json()
        change(saved)
        assert client.put("/api/voice/v1/settings", json=saved).status_code == 422
    assert not config.settings_file.exists()


def test_unknown_stored_version_fails_without_overwrite(tmp_path):
    config = configuration(tmp_path)
    original = '{"schemaVersion":9,"revision":42}'
    config.settings_file.write_text(original)
    with pytest.raises(ValueError):
        load_profiles(config)
    assert config.settings_file.read_text() == original
    assert not config.legacy_settings_backup.exists()


async def test_inactive_profile_edit_does_not_reconfigure_a_live_conversation(
    tmp_path, monkeypatch
):
    manager = Supervisor(configuration(tmp_path))
    manager.state, manager.active = "ready", True

    async def forbidden(*args, **kwargs):
        raise AssertionError("Inactive profile edits must not touch the worker")

    monkeypatch.setattr(manager.client, "put", forbidden)
    try:
        proposed = manager.configuration.model_copy(deep=True)
        proposed.profiles.lfm3_audio.voice = "male"
        result = await manager.update_profiles(proposed)
        assert result["revision"] == 1 and manager.active
        assert manager.settings.voice == "NATF0"
        with pytest.raises(HTTPException) as error:
            await manager.update_settings(VoiceSettings(voice="NATM1"))
        assert error.value.status_code == 409
    finally:
        await manager.close()


async def test_worker_reconfiguration_receives_only_legacy_settings(tmp_path, monkeypatch):
    manager = Supervisor(configuration(tmp_path))
    manager.state = "ready"
    calls = []

    async def update(path, **kwargs):
        calls.append((path, kwargs["json"]))
        return httpx.Response(200, json=kwargs["json"])

    monkeypatch.setattr(manager.client, "put", update)
    try:
        value = manager.configuration.model_copy(deep=True)
        value.profiles.personaplex.voice = "NATM2"
        value.profiles.lfm3_audio.voice = "male"
        await manager.update_profiles(value)
        assert calls == [
            ("/settings", {"voice": "NATM2", "prompt": manager.settings.prompt, "bits": 4})
        ]
        assert config_document(manager.config)["profiles"]["lfm3-audio"]["voice"] == "male"
    finally:
        await manager.close()


def config_document(config):
    return json.loads(config.settings_file.read_text())


def test_legacy_audio_rejects_incapable_selected_provider_before_reserving_session(tmp_path):
    manager = Supervisor(configuration(tmp_path))
    manager.configuration = VoiceConfiguration(selectedProvider="lfm3-audio")
    manager.state = "ready"
    with TestClient(create_app(manager.config, manager)) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive()["code"] == 1003
        assert not manager.active


def test_diagnostics_report_the_selected_provider_assets(tmp_path, monkeypatch):
    from controller import autostart

    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    manager = Supervisor(configuration(tmp_path))
    manager.configuration = VoiceConfiguration(selectedProvider="lfm3-audio")
    with TestClient(create_app(manager.config, manager)) as client:
        response = client.get("/api/diagnostics")
        assert response.status_code == 200
        assert "installed.json" in response.json()["missingFiles"]
        assert not response.json()["autostartEnabled"]
    assert not manager.config.weights_dir.exists()


async def test_two_clients_cannot_overwrite_each_others_profile_changes(tmp_path):
    manager = Supervisor(configuration(tmp_path))
    first = manager.configuration.model_copy(deep=True)
    second = manager.configuration.model_copy(deep=True)
    first.profiles.personaplex.prompt = "First client"
    second.profiles.personaplex.prompt = "Second client"
    try:
        results = await asyncio.gather(
            manager.update_profiles(first), manager.update_profiles(second), return_exceptions=True
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        errors = [result for result in results if isinstance(result, HTTPException)]
        assert len(errors) == 1 and errors[0].status_code == 409
        assert manager.configuration.revision == 1
        assert manager.config.load_settings().prompt == "First client"
    finally:
        await manager.close()
