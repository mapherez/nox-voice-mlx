"""Exercise the real TLS and WebSocket hops with an echo worker, not model weights."""

import base64
import json
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
from controller.model_library import ModelLibrary
from controller.profiles import VoiceConfiguration
from controller.server import create_app as controller_app
from controller.supervisor import Supervisor
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from nox_voice_gateway.app import create_app as gateway_app
from nox_voice_gateway.sessions import COOKIE
from starlette.websockets import WebSocketDisconnect


def test_lfm3_transcript_executor_dependent_mcp_calls_and_browser_ownership(config, tmp_path):
    from controller.executor.config import ExecutorSettings
    from controller.profiles import LFM3Settings
    from engine.lfm3.server import create_app as audio_app

    external = FastAPI()
    tool_calls, llm_requests = [], []
    bearer = "fixture-private-mcp-key"
    transcript = 'Procura os itens com o título "Olá Mundo" e remove os resultados.'
    tool_definitions = [
        {"name": "search", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
        {"name": "remove", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "revision": {"type": "integer"}}, "required": ["id", "revision"]}},
    ]

    @external.post("/mcp")
    async def mcp(request: Request):
        assert request.headers.get("authorization") == "Bearer " + bearer
        body = await request.json()
        if "id" not in body:
            from fastapi.responses import Response
            return Response(status_code=202)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": "Unrelated generic fixture", "version": "1"}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": tool_definitions}
        else:
            assert method == "tools/call"
            tool_calls.append(body["params"])
            result = {"content": [], "structuredContent": {"id": "authoritative-id", "revision": 19, "echo": bearer}}
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    @external.get("/v1/models")
    async def models():
        return {"data": [{"id": "fixture-executor"}]}

    @external.post("/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        llm_requests.append(body)
        assert bearer not in json.dumps(body)
        messages = body["messages"]
        results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        if len(results) < 2:
            index = len(results)
            arguments = {"arguments": {"title": "Olá Mundo"}} if index == 0 else {"arguments": {}, "bindings": [
                {"target": "/id", "reference": results[-1]["reference"], "pointer": "/id"},
                {"target": "/revision", "reference": results[-1]["reference"], "pointer": "/revision"}]}
            answer = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{index}", "type": "function",
                "function": {"name": body["tools"][index]["function"]["name"], "arguments": json.dumps(arguments)}}]}
        else:
            answer = {"role": "assistant", "content": json.dumps({"status": "succeeded", "output": "Os itens foram removidos.", "speech": "Feito."})}
        return {"choices": [{"finish_reason": "tool_calls" if "tool_calls" in answer else "stop", "message": answer}]}

    class Runtime:
        ready, process = True, None

        async def transcribe(self, audio, settings):
            while await audio.get() is not None:
                pass
            return transcript

        async def synthesize(self, text, settings):
            assert text == "Feito."
            yield base64.b64encode(b"\x00\x00" * 10).decode()

    worker_token = "owned-fixture-worker-token-" * 2
    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    with running(external) as external_port, running(audio_app(Runtime(), LFM3Settings(), worker_token)) as worker_port:
        native = ControllerConfig(worker_port=worker_port, data_dir=tmp_path / "native")
        manager = Supervisor(native)
        manager.configuration = VoiceConfiguration(selectedProvider="lfm3-audio", revision=3)
        manager.state, manager._token = "ready", worker_token
        with running(controller_app(native, manager), ssl_certfile=str(bundle.parent / "server.crt"),
                ssl_keyfile=str(bundle.parent / "server.key"), ssl_ca_certs=str(bundle / "ca.crt"), ssl_cert_reqs=ssl.CERT_REQUIRED) as port:
            web_config = replace(config, controller_url=f"https://127.0.0.1:{port}",
                ca_file=bundle / "ca.crt", cert_file=bundle / "client.crt", key_file=bundle / "client.key")
            app = gateway_app(web_config)
            with TestClient(app, base_url=config.public_url) as client:
                token = app.state.sessions.create(config.owner_email, "fixture", config.public_url)
                client.cookies.set(COOKIE, token, domain="voice.test", path="/")
                headers = {"Origin": config.public_url, "X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}
                executor = ExecutorSettings(enabled=True, llm={"model": "fixture-executor", "baseUrl": f"http://127.0.0.1:{external_port}/v1"},
                    connections=[{"id": "fixture", "name": "Fixture", "url": f"http://127.0.0.1:{external_port}/mcp", "credential": "mcp-fixture"}])
                assert client.put("/api/voice/v1/executor/settings", json=executor.model_dump(), headers=headers).status_code == 200
                assert client.put("/api/voice/v1/credentials/mcp-fixture", json={"secret": bearer}, headers=headers).status_code == 200
                assert bearer not in client.get("/api/voice/v1/credentials").text
                assert client.post("/api/voice/v1/executor/test", headers=headers).json()["selectedModelAvailable"]
                assert client.post("/api/voice/v1/executor/connections/fixture/connect", headers=headers).status_code == 200
                assert tool_calls == [] and llm_requests == []
                with client.websocket_connect("wss://voice.test/voice/v1/ws", headers={"Origin": config.public_url, "Cookie": f"{COOKIE}={token}"}) as wire:
                    wire.send_json({"type": "session.start", "version": 1})
                    ready = wire.receive_json()
                    assert ready["capabilities"]["executor"] and ready["settingsRevision"] == 3
                    session_id = ready["sessionId"]
                    control = {"sessionId": session_id, "turnId": 1}
                    wire.send_json({**control, "type": "turn.start"})
                    wire.send_json({**control, "type": "input.audio", "audio": base64.b64encode(b"\x00\x00" * 160).decode()})
                    wire.send_json({**control, "type": "turn.commit"})
                    events = []
                    for _ in range(20):
                        event = wire.receive_json()
                        events.append(event)
                        if event["type"] == "task.updated" and event["task"]["state"] == "succeeded":
                            break
                    else:
                        pytest.fail("Executor did not finish the fixture task")
                    task_id = events[-1]["task"]["taskId"]
                    assert tool_calls[1]["arguments"] == {"id": "authoritative-id", "revision": 19}
                    assert len(tool_calls) == 2
                    assert any(event.get("text") == transcript for event in events)
                    assert [event["sequence"] for event in events] == sorted(set(event["sequence"] for event in events))
                    path = f"/api/voice/v1/sessions/{session_id}/tasks/{task_id}"
                    detail = client.get(path)
                    assert detail.json()["transcript"] == transcript and detail.json()["state"] == "succeeded"
                    assert bearer not in detail.text and "[redacted]" in detail.text
                    other_token = app.state.sessions.create(config.owner_email, "another-device", config.public_url)
                    client.cookies.set(COOKIE, other_token, domain="voice.test", path="/")
                    assert client.get(path, headers={"X-NoX-Session-Owner": next(iter(manager.executor.stores.values())).owner}).status_code == 404
                    client.cookies.set(COOKIE, token, domain="voice.test", path="/")
                    wire.send_json({"type": "speech.request", "sessionId": session_id, "playbackId": 1, "text": "Feito."})
                    speech = [wire.receive_json() for _ in range(3)]
                    assert [event["type"] for event in speech] == ["speech.started", "speech.audio", "speech.ended"]


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


def test_clear_download_history_through_gateway_and_real_mtls(config, tmp_path):
    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    native_config = ControllerConfig(
        data_dir=tmp_path / "native", weights_dir=tmp_path / "weights"
    )
    manager = Supervisor(native_config)
    model = native_config.weights_dir / "lfm3-audio/models/model.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model fixture must survive clearing history")
    manager.library.jobs["finished"] = {
        "id": "finished", "provider": "lfm3-audio", "state": "completed"
    }
    manager.library.jobs["active"] = {
        "id": "active", "provider": "personaplex", "state": "downloading"
    }
    manager.library.save(True)
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
            token = app.state.sessions.create(config.owner_email, "fixture", config.public_url)
            client.cookies.set(COOKIE, token, domain="voice.test", path="/")
            csrf = client.get("/api/session").json()["csrfToken"]
            result = client.delete(
                "/api/voice/v1/downloads",
                headers={"Origin": config.public_url, "X-CSRF-Token": csrf},
            )
            assert result.status_code == 200
            assert result.headers["content-type"].startswith("application/json")
            assert [job["id"] for job in result.json()["jobs"]] == ["active"]
            assert client.get("/api/voice/v1/downloads").json() == result.json()
            assert set(ModelLibrary(manager).jobs) == {"active"}
            assert model.read_bytes() == b"model fixture must survive clearing history"


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
            await websocket.send_json({"type": "session.ready", "sessionId": "fixture", "capabilities": {"executor": False}})
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
                        assert stream.receive_json() == {**frame, "sequence": 2}
                    else:
                        frame = b"\x00" * 7680
                        stream.send_bytes(frame)
                        assert stream.receive_bytes() == frame
                    assert manager.active
