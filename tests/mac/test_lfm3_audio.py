import asyncio
import base64
import json

import pytest
from controller.profiles import LFM3Settings
from engine.lfm3.server import create_app
from engine.lfm3.session import AudioSession
from fastapi.testclient import TestClient


class AudioRuntime:
    ready = True
    process = None

    def __init__(self):
        self.calls = []
        self.cancelled = 0

    async def transcribe(self, queue, settings):
        received = []
        try:
            while (packet := await queue.get()) is not None:
                received.append(packet)
            self.calls.append(("asr", received, settings.inputLanguage))
            return 'Find everything titled "Hello World" and delete the results.'
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def synthesize(self, text, settings):
        self.calls.append(("tts", text, settings.voice))
        try:
            yield base64.b64encode(b"\0\0" * 10).decode()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


async def spin():
    for _ in range(6):
        await asyncio.sleep(0)


async def test_speech_interrupts_before_new_capture_and_stale_frames_are_ignored():
    runtime, events = AudioRuntime(), []

    async def emit(value):
        events.append(value)

    session = AudioSession(runtime, LFM3Settings(inputLanguage="Portuguese"), emit)
    try:
        await session.ready()
        await session.handle(
            {
                "type": "speech.request",
                "sessionId": session.id,
                "playbackId": 1,
                "text": "Text chosen by the user",
            }
        )
        await spin()
        assert any(event["type"] == "speech.audio" for event in events)
        await session.handle({"type": "turn.start", "sessionId": session.id, "turnId": 1})
        assert runtime.cancelled == 1
        await session.handle({"type": "turn.start", "sessionId": session.id, "turnId": 2})
        await session.handle(
            {
                "type": "input.audio",
                "sessionId": session.id,
                "turnId": 1,
                "audio": "invalid stale packet",
            }
        )
        packet = base64.b64encode(b"\0\0" * 200).decode()
        await session.handle(
            {"type": "input.audio", "sessionId": session.id, "turnId": 2, "audio": packet}
        )
        await session.handle({"type": "turn.commit", "sessionId": session.id, "turnId": 2})
        await spin()
        assert events[-1]["type"] == "transcript.completed"
        assert events[-1]["turnId"] == 2 and '"Hello World"' in events[-1]["text"]
        assert runtime.calls[-1] == ("asr", [packet], "Portuguese")
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    finally:
        await session.close()
    assert session.operation is None


async def test_empty_turn_does_not_transcribe_or_execute_and_invalid_audio_is_rejected():
    runtime, events = AudioRuntime(), []

    async def emit(value):
        events.append(value)

    session = AudioSession(runtime, LFM3Settings(), emit)
    try:
        await session.handle({"type": "turn.start", "sessionId": session.id, "turnId": 1})
        for packet in [
            "!invalid",
            base64.b64encode(b"a").decode(),
            base64.b64encode(b"aa" * 1601).decode(),
        ]:
            with pytest.raises(ValueError):
                await session.handle(
                    {"type": "input.audio", "sessionId": session.id, "turnId": 1, "audio": packet}
                )
        await session.handle({"type": "turn.commit", "sessionId": session.id, "turnId": 1})
        assert events[-1]["text"] == "" and runtime.calls == []
        with pytest.raises(ValueError):
            await session.handle(
                {"type": "turn.start", "sessionId": "another session", "turnId": 2}
            )
    finally:
        await session.close()


def test_private_worker_requires_bearer_and_versioned_handshake_and_releases_session():
    runtime = AudioRuntime()
    token = "worker-test-token-" * 3
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(runtime, LFM3Settings(), token)) as client:
        assert client.get("/health").status_code == 401
        with pytest.raises(Exception) as error:
            with client.websocket_connect("/voice/v1/ws"):
                pass
        assert getattr(error.value, "code", None) == 1008
        with client.websocket_connect("/voice/v1/ws", headers=headers) as stream:
            stream.send_json({"type": "session.start", "version": 1})
            event = stream.receive_json()
            assert event["capabilities"] == {
                "transcription": True,
                "speech": True,
                "executor": False,
            }
            assert client.get("/health", headers=headers).json()["conversationActive"]
            assert (
                client.put(
                    "/settings", json=LFM3Settings(voice="male").model_dump(), headers=headers
                ).status_code
                == 409
            )
            with client.websocket_connect("/voice/v1/ws", headers=headers) as second:
                assert second.receive()["code"] == 1013
            stream.send_json({"type": "turn.start", "sessionId": event["sessionId"], "turnId": 1})
            assert stream.receive_json()["type"] == "turn.started"
            assert client.get("/health", headers=headers).status_code == 200
            stream.send_json(
                {
                    "type": "input.audio",
                    "sessionId": event["sessionId"],
                    "turnId": 1,
                    "audio": "!invalid",
                }
            )
            assert stream.receive_json()["code"] == "INVALID_CONTROL"
            assert runtime.cancelled == 1
            stream.send_text("not json")
            assert stream.receive()["code"] == 1003
        assert not client.get("/health", headers=headers).json()["conversationActive"]
        assert (
            client.put(
                "/settings", json=LFM3Settings(voice="male").model_dump(), headers=headers
            ).status_code
            == 200
        )
        with client.websocket_connect("/voice/v1/ws", headers=headers) as stream:
            stream.send_text(json.dumps({"type": "session.start", "version": 1}))
            assert stream.receive_json()["type"] == "session.ready"
