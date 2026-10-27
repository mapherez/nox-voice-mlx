import asyncio
from contextlib import asynccontextmanager

from controller.config import ControllerConfig
from controller.server import create_app
from controller.supervisor import Supervisor
from fastapi.testclient import TestClient


class EchoStream:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def send(self, value):
        await self.queue.put(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()


def test_second_connection_is_rejected_without_touching_active_session(tmp_path, monkeypatch):
    import controller.server as server

    config = ControllerConfig(data_dir=tmp_path)
    manager = Supervisor(config)
    manager.state = "ready"

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield EchoStream()

    monkeypatch.setattr(server, "connect", connect)
    with TestClient(create_app(config, manager)) as client:
        with client.websocket_connect("/ws") as first:
            assert manager.active
            with client.websocket_connect("/ws") as second:
                event = second.receive()
                assert event["type"] == "websocket.close" and event["code"] == 1013
            first.send_bytes(b"\x00" * 7680)
            assert first.receive_bytes() == b"\x00" * 7680
            assert manager.active


def test_loopback_worker_requires_internal_bearer(tmp_path, monkeypatch):
    from engine.server import create_app as engine_app

    monkeypatch.setenv("NOX_VOICE_WORKER_TOKEN", "test-token-" * 8)
    settings = tmp_path / "settings.json"
    settings.write_text('{"voice":"NATF0","prompt":"Test","bits":4}')
    monkeypatch.setenv("NOX_VOICE_ENGINE_SETTINGS", str(settings))
    # Deliberately no lifespan: the auth boundary does not need or load weights.
    client = TestClient(engine_app())
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/health", headers={"Authorization": "Bearer " + "test-token-" * 8}).json()[
            "ready"
        ]
        is False
    )
