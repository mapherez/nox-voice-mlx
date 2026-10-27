import asyncio
import os

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
        assert len(kwargs["env"]["NOX_VOICE_WORKER_TOKEN"]) >= 32
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
