import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx2 as httpx
import pytest
from controller.config import ControllerConfig, VoiceSettings
from controller.supervisor import Supervisor


class FakeProcess:
    def __init__(self):
        self.pid = os.getpid()
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.done = asyncio.Event()
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.done.set()

    def kill(self):
        self.terminate()

    async def wait(self):
        await self.done.wait()
        return self.returncode


@pytest.fixture
def supervisor(tmp_path):
    return Supervisor(ControllerConfig(data_dir=tmp_path, weights_dir=tmp_path / "weights"))


async def test_start_is_idempotent_and_stop_cancels_loading(supervisor, monkeypatch):
    calls = []
    process = FakeProcess()

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("HF_TOKEN", "never-forward-this-token")
    try:
        assert (await supervisor.start())["state"] == "starting"
        await supervisor.start()
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[1:3] == ("-m", "engine.server")
        assert "HF_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["env"]["NOX_VOICE_WEIGHTS_DIR"] == str(
            supervisor.config.weights_dir / "personaplex"
        )
        assert kwargs["env"]["NOX_VOICE_PROVIDER_ASSETS"] == str(
            supervisor.config.weights_dir / "personaplex"
        )
        assert len(kwargs["env"]["NOX_VOICE_WORKER_TOKEN"]) >= 32
        assert set(json.loads(Path(kwargs["env"]["NOX_VOICE_ENGINE_SETTINGS"]).read_text())) == {
            "voice",
            "prompt",
            "bits",
        }
        assert json.loads(supervisor.config.settings_file.read_text())["schemaVersion"] == 1
        assert (await supervisor.stop())["state"] == "stopped"
        assert process.terminated
    finally:
        await supervisor.close()


async def test_ready_requires_authenticated_worker_health(supervisor, monkeypatch):
    process = FakeProcess()

    async def spawn(*args, **kwargs):
        return process

    async def health(*args, **kwargs):
        assert kwargs["headers"]["Authorization"] == f"Bearer {supervisor._token}"
        return httpx.Response(200, json={"ready": True})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(supervisor.client, "get", health)
    try:
        await supervisor.start()
        for _ in range(10):
            await asyncio.sleep(0)
            if supervisor.state == "ready":
                break
        assert supervisor.state == "ready"
        process.stdout.feed_data(b'TOP SECRET PROMPT\n{"phase":"voice"}\n')
        await asyncio.sleep(0)
        assert "TOP SECRET" not in str(supervisor.events)
    finally:
        await supervisor.close()


async def test_settings_cannot_change_during_a_conversation(supervisor):
    from fastapi import HTTPException

    supervisor.active = True
    try:
        with pytest.raises(HTTPException) as error:
            await supervisor.update_settings(VoiceSettings(voice="NATM1"))
        assert error.value.status_code == 409
        assert supervisor.settings.voice == "NATF0"
    finally:
        supervisor.active = False
        await supervisor.close()


async def test_settings_are_persisted_without_logging_the_prompt(supervisor):
    try:
        await supervisor.update_settings(VoiceSettings(prompt="A private persona"))
        assert supervisor.config.load_settings().prompt == "A private persona"
        assert "A private persona" not in str(supervisor.events)
        assert supervisor.config.settings_file.stat().st_mode & 0o077 == 0
    finally:
        await supervisor.close()


async def test_stop_remains_available_during_long_reconfiguration(supervisor, monkeypatch):
    from fastapi import HTTPException

    entered, complete = asyncio.Event(), asyncio.Event()
    process = FakeProcess()
    supervisor.process, supervisor.state = process, "ready"

    async def reconfigure(*args, **kwargs):
        entered.set()
        await complete.wait()
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(supervisor.client, "put", reconfigure)
    change = asyncio.create_task(supervisor.update_settings(VoiceSettings(voice="NATM1")))
    try:
        await entered.wait()
        assert supervisor.status()["settingsChanging"] is True
        assert (await asyncio.wait_for(supervisor.stop(), timeout=1))["state"] == "stopped"
        assert process.terminated
        complete.set()
        with pytest.raises(HTTPException) as error:
            await change
        assert error.value.status_code == 409
        assert supervisor.settings.voice == "NATF0"
    finally:
        complete.set()
        await asyncio.gather(change, return_exceptions=True)
        await supervisor.close()


async def test_shutdown_ejects_model_even_if_download_metadata_cannot_be_saved(supervisor, monkeypatch):
    process = FakeProcess()
    supervisor.process, supervisor.state = process, "ready"

    async def failed_download_close():
        raise OSError("fixture full disk")

    monkeypatch.setattr(supervisor.library, "close", failed_download_close)
    with pytest.raises(OSError, match="full disk"):
        await supervisor.close()
    assert process.terminated and supervisor.state == "stopped"
    assert supervisor.client.is_closed


async def test_provider_switch_ejects_then_requires_explicit_load_with_its_own_profile(
    supervisor, monkeypatch
):
    from controller.providers import PROVIDERS, Provider

    # Exercise the switch using an explicitly fake adapter, not an advertised LFM3 implementation.
    monkeypatch.setitem(
        PROVIDERS,
        "lfm3-audio",
        replace(PROVIDERS["lfm3-audio"], worker_module="engine.test_worker"),
    )
    monkeypatch.setattr(Provider, "missing_assets", lambda *_: [])
    previous, incoming = FakeProcess(), FakeProcess()
    supervisor.process, supervisor.state = previous, "ready"
    supervisor.running_provider = "personaplex"
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return incoming

    async def health(*args, **kwargs):
        return httpx.Response(200, json={"ready": True})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(supervisor.client, "get", health)
    try:
        proposed = supervisor.configuration.model_copy(deep=True)
        proposed.selectedProvider = "lfm3-audio"
        await supervisor.update_profiles(proposed)
        assert previous.terminated
        assert calls == [] and supervisor.state == "stopped"
        await supervisor.start()
        assert len(calls) == 1 and calls[0][0][2] == "engine.test_worker"
        bootstrap = Path(calls[0][1]["env"]["NOX_VOICE_ENGINE_SETTINGS"])
        assert bootstrap.name == "lfm3-audio-settings.json"
        assert json.loads(bootstrap.read_text())["voice"] == "female"
        assert "bits" not in json.loads(bootstrap.read_text())
        assert supervisor.status()["activeProvider"] == "lfm3-audio"
        assert calls[0][1]["env"]["NOX_VOICE_PROVIDER_ASSETS"] == str(
            supervisor.config.weights_dir / "lfm3-audio"
        )
    finally:
        await supervisor.close()


async def test_selecting_unprepared_model_stays_stopped_without_spawning_or_download(
    supervisor, monkeypatch
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Model selection must not spawn or download assets")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    previous = FakeProcess()
    supervisor.process, supervisor.state, supervisor.running_provider = (
        previous,
        "ready",
        "personaplex",
    )
    try:
        proposed = supervisor.configuration.model_copy(deep=True)
        proposed.selectedProvider = "lfm3-audio"
        await supervisor.update_profiles(proposed)
        assert previous.terminated
        assert supervisor.state == "stopped"
        assert supervisor.status()["modelFilesMissing"]
        assert not supervisor.config.weights_dir.exists()
    finally:
        await supervisor.close()


async def test_explicit_preparation_owns_one_child_filters_logs_and_stop_cancels_it(
    supervisor, monkeypatch
):
    from fastapi import HTTPException

    process, calls = FakeProcess(), []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("HF_TOKEN", "unrelated-secret-never-forward")
    try:
        assert (await supervisor.prepare_provider("lfm3-audio"))["preparation"][
            "state"
        ] == "preparing"
        assert calls[0][0][1:3] == ("-m", "engine.lfm3.assets")
        assert calls[0][0][-1] == str(supervisor.config.weights_dir / "lfm3-audio")
        assert "HF_TOKEN" not in calls[0][1]["env"]
        with pytest.raises(HTTPException) as error:
            await supervisor.prepare_provider("lfm3-audio")
        assert error.value.status_code == 409 and len(calls) == 1
        process.stdout.feed_data(b'{"phase":"models"}\nDO NOT FORWARD PRIVATE DATA\n')
        await asyncio.sleep(0)
        assert supervisor.status()["preparation"]["phase"] == "Preparing model weights"
        assert "PRIVATE DATA" not in str(supervisor.events)
        await supervisor.stop()
        assert process.terminated
        assert supervisor.status()["preparation"]["state"] == "cancelled"
        assert supervisor.state == "stopped"
    finally:
        await supervisor.close()
