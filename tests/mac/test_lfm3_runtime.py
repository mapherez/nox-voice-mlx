import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from controller.profiles import LFM3Settings
from engine.lfm3.runtime import LFM3Runtime, runtime_environment
from websockets.asyncio.server import serve


async def test_native_protocol_preserves_complete_turn_and_uses_audio_only_tts(tmp_path):
    sessions = []

    async def native(wire):
        assert wire.request.headers["Authorization"] == "Bearer private-fixture-key"
        await wire.send(
            json.dumps(
                {
                    "type": "session.created",
                    "session": {
                        "audio": {
                            "input": {"format": {"rate": 16000}},
                            "output": {"format": {"rate": 24000}},
                        }
                    },
                }
            )
        )
        record = []
        sessions.append(record)
        async for raw in wire:
            event = json.loads(raw)
            record.append(event)
            if event["type"] == "session.update":
                await wire.send('{"type":"session.updated"}')
            elif event["type"] == "response.create":
                mode = record[0]["session"]["output_modalities"]
                if mode == ["text"]:
                    await wire.send(
                        json.dumps(
                            {
                                "type": "response.output_text.delta",
                                "delta": 'Encontra as notas com o título "Hello World" e apaga os resultados.',
                            }
                        )
                    )
                else:
                    await wire.send(
                        json.dumps(
                            {
                                "type": "response.output_audio.delta",
                                "delta": base64.b64encode(b"\0\0" * 4800).decode(),
                            }
                        )
                    )
                await wire.send('{"type":"response.done","response":{"status":"completed"}}')

    runtime = LFM3Runtime(tmp_path)
    runtime.key, runtime.process = "private-fixture-key", SimpleNamespace(returncode=None)
    async with serve(native, "127.0.0.1", 0) as server:
        runtime.port = server.sockets[0].getsockname()[1]
        queue = asyncio.Queue()
        packet = base64.b64encode(b"\0\0" * 500).decode()
        queue.put_nowait(packet)
        queue.put_nowait(None)
        settings = LFM3Settings(inputLanguage="Portuguese", vocabulary="NoX Suite")
        text = await runtime.transcribe(queue, settings)
        assert '"Hello World"' in text and "apaga os resultados" in text
        assert sessions[0][0]["session"]["instructions"].startswith("Perform ASR in Portuguese.")
        assert sessions[0][0]["session"]["tool_choice"] == "none"
        assert sessions[0][1] == {"type": "input_audio_buffer.append", "audio": packet}
        speech = [
            base64.b64decode(value)
            async for value in runtime.synthesize("Read exactly this text.", settings)
        ]
        assert b"".join(speech) == b"\0\0" * 4800
        assert all(len(part) <= 3840 for part in speech)
        assert sessions[1][0]["session"]["output_modalities"] == ["audio"]
        assert (
            sessions[1][0]["session"]["instructions"]
            == "Perform TTS in American English using a female voice."
        )
        assert sessions[1][1]["item"]["content"] == [
            {"type": "input_text", "text": "Read exactly this text."}
        ]
    runtime.process = None
    await runtime.close()
    assert not runtime.slot.locked() and not runtime.connections


async def test_cancelling_native_session_closes_socket_and_releases_model_slot(tmp_path):
    connected, disconnected = asyncio.Event(), asyncio.Event()

    async def native(wire):
        try:
            await wire.send(
                json.dumps(
                    {
                        "type": "session.created",
                        "session": {
                            "audio": {
                                "input": {"format": {"rate": 16000}},
                                "output": {"format": {"rate": 24000}},
                            }
                        },
                    }
                )
            )
            await wire.recv()
            await wire.send('{"type":"session.updated"}')
            connected.set()
            await wire.wait_closed()
        finally:
            disconnected.set()

    runtime = LFM3Runtime(tmp_path)
    runtime.process = SimpleNamespace(returncode=None)
    async with serve(native, "127.0.0.1", 0) as server:
        runtime.port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(runtime.transcribe(asyncio.Queue(), LFM3Settings()))
        await asyncio.wait_for(connected.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(disconnected.wait(), 2)
    runtime.process = None
    await runtime.close()
    assert not runtime.slot.locked() and not runtime.connections


def test_runtime_has_isolated_caches_and_no_inherited_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "not-for-native-runtime")
    environment = runtime_environment(tmp_path, "native-private-key")
    assert environment["LLAMA_API_KEY"] == "native-private-key"
    for name in ["TMPDIR", "XDG_CACHE_HOME", "HF_HOME", "HF_HUB_CACHE", "LLAMA_CACHE"]:
        assert environment[name].startswith(str(tmp_path) + "/")
    assert "HF_TOKEN" not in environment and "HOME" not in environment
