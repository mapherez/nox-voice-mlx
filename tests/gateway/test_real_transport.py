"""Exercise the real TLS and WebSocket hops with an echo worker, not model weights."""

import socket
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest
import uvicorn
from controller.certificates import create_certificates
from controller.config import ControllerConfig
from controller.profiles import VoiceConfiguration
from controller.server import create_app as controller_app
from controller.supervisor import Supervisor
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app as gateway_app
from nox_voice_gateway.sessions import COOKIE
from starlette.websockets import WebSocketDisconnect


@contextmanager
def running(app, **options):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, ws_max_size=65536, **options)
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive(), "Test server exited"
            assert time.monotonic() < deadline, "Test server did not start"
            time.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "Test server did not stop"


@pytest.mark.parametrize("versioned", [False, True])
def test_browser_gateway_mtls_controller_and_private_worker(config, tmp_path, versioned):
    worker = FastAPI()
    bearer = "private-worker-test-token"

    @worker.websocket("/ws")
    async def echo(websocket: WebSocket):
        if websocket.headers.get("authorization") != f"Bearer {bearer}":
            await websocket.close(1008)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_bytes(await websocket.receive_bytes())
        except WebSocketDisconnect:
            pass

    @worker.websocket("/voice/v1/ws")
    async def echo_versioned(websocket: WebSocket):
        if websocket.headers.get("authorization") != f"Bearer {bearer}":
            await websocket.close(1008)
            return
        await websocket.accept()
        try:
            assert await websocket.receive_json() == {"type": "session.start", "version": 1}
            await websocket.send_json({"type": "session.ready", "sessionId": "fixture"})
            while True:
                await websocket.send_json(await websocket.receive_json())
        except WebSocketDisconnect:
            pass

    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    with running(worker) as worker_port:
        native_config = ControllerConfig(worker_port=worker_port, data_dir=tmp_path / "native")
        manager = Supervisor(native_config)
        if versioned:
            manager.configuration = VoiceConfiguration(selectedProvider="lfm3-audio", revision=7)
        manager.state, manager._token = "ready", bearer
        with running(
            controller_app(native_config, manager),
            ssl_certfile=str(bundle.parent / "server.crt"),
            ssl_keyfile=str(bundle.parent / "server.key"),
            ssl_ca_certs=str(bundle / "ca.crt"),
            ssl_cert_reqs=ssl.CERT_REQUIRED,
        ) as controller_port:
            web_config = replace(
                config,
                controller_url=f"https://127.0.0.1:{controller_port}",
                ca_file=bundle / "ca.crt",
                cert_file=bundle / "client.crt",
                key_file=bundle / "client.key",
            )
            app = gateway_app(web_config)
            with TestClient(app, base_url=config.public_url) as client:
                token = app.state.sessions.create(
                    config.owner_email, "test-subject", config.public_url
                )
                client.cookies.set(COOKIE, token, domain="voice.test", path="/")
                with client.websocket_connect(
                    "wss://voice.test" + ("/voice/v1/ws" if versioned else "/ws"),
                    headers={"Origin": config.public_url, "Cookie": f"{COOKIE}={token}"},
                ) as stream:
                    if versioned:
                        stream.send_json({"type": "session.start", "version": 1})
                        assert stream.receive_json()["settingsRevision"] == 7
                        frame = {"type": "fixture.large", "value": "a" * 12000}
                        stream.send_json(frame)
                        assert stream.receive_json() == frame
                    else:
                        frame = b"\x00" * 7680
                        stream.send_bytes(frame)
                        assert stream.receive_bytes() == frame
                    assert manager.active
