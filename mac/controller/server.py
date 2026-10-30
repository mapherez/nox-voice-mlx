"""mTLS controller API. Run using the TLS-enforcing CLI, never bare uvicorn."""

import asyncio
import json
from contextlib import asynccontextmanager, suppress

import anyio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .config import ControllerConfig, VoiceSettings
from .credentials import SERVICES, CredentialWrite
from .profiles import ConfigurationUpdate, ProviderId, VoiceConfiguration
from .providers import provider
from .supervisor import Supervisor


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: ProviderId


def create_app(config: ControllerConfig, supervisor: Supervisor | None = None) -> FastAPI:
    manager = supervisor or Supervisor(config)

    @asynccontextmanager
    async def lifespan(_):
        yield
        await manager.close()

    app = FastAPI(
        title="NoX Voice Controller",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    app.state.manager = manager

    @app.get("/api/engine/status")
    async def status():
        return await manager.refresh_status()

    @app.post("/api/engine/start", status_code=202)
    async def start():
        return await manager.start()

    @app.post("/api/engine/stop", status_code=202)
    async def stop():
        return await manager.stop()

    @app.post("/api/engine/restart", status_code=202)
    async def restart():
        return await manager.restart()

    @app.get("/api/settings")
    async def settings():
        return manager.settings.model_dump()

    @app.put("/api/settings")
    async def update(settings: VoiceSettings):
        return await manager.update_settings(settings)

    @app.get("/api/voice/v1/providers")
    async def providers():
        return manager.providers()

    @app.get("/api/voice/v1/settings")
    async def profiles():
        return manager.profiles()

    @app.put("/api/voice/v1/settings")
    async def update_profiles(value: ConfigurationUpdate):
        return await manager.update_profiles(VoiceConfiguration.model_validate(value.document()))

    @app.get("/api/voice/v1/engine/status")
    async def provider_status():
        return await manager.refresh_status()

    @app.post("/api/voice/v1/engine/{action}", status_code=202)
    async def provider_action(action: str):
        if action not in {"start", "stop", "restart", "load", "eject"}:
            raise HTTPException(404, "Unknown engine action.")
        if action == "load" and (manager.active or manager.reconfiguring):
            raise HTTPException(409, "End the conversation before loading a model.")
        return await getattr(manager, {"load": "start", "eject": "stop"}.get(action, action))()

    @app.get("/api/voice/v1/models")
    async def models():
        return manager.library.models()

    @app.post("/api/voice/v1/providers/{provider_id}/open-folder")
    async def open_folder(provider_id: ProviderId):
        return await manager.library.open_folder(provider_id)

    @app.get("/api/voice/v1/downloads")
    async def downloads():
        return manager.library.snapshot()

    @app.post("/api/voice/v1/downloads", status_code=202)
    async def download(value: DownloadRequest):
        async with manager.lock:
            return await manager.library.download(value.provider)

    @app.post("/api/voice/v1/downloads/{download_id}/{action}")
    async def download_action(download_id: str, action: str):
        if action == "cancel":
            return await manager.library.cancel(download_id)
        if action == "retry":
            async with manager.lock:
                return await manager.library.retry(download_id)
        raise HTTPException(404, "Unknown download action.")

    @app.get("/api/voice/v1/credentials")
    async def credentials():
        return manager.library.credentials.describe()

    def credential_service(service):
        if service not in SERVICES:
            raise HTTPException(404, "Unknown credential service.")
        return manager.library.credentials

    @app.put("/api/voice/v1/credentials/{service}")
    async def save_credential(service: str, value: CredentialWrite):
        try:
            return credential_service(service).save(service, value.secret.get_secret_value())
        except ValueError:
            raise HTTPException(422, "Enter a valid Hugging Face token beginning with hf_.") from None

    @app.delete("/api/voice/v1/credentials/{service}")
    async def remove_credential(service: str):
        return credential_service(service).remove(service)

    @app.post("/api/voice/v1/credentials/{service}/test")
    async def test_credential(service: str):
        return await asyncio.to_thread(credential_service(service).test, service)

    @app.post("/api/voice/v1/providers/{provider_id}/prepare", status_code=202)
    async def prepare(provider_id: ProviderId):
        return await manager.prepare_provider(provider_id)

    @app.get("/api/diagnostics")
    async def diagnostics():
        from .autostart import is_enabled

        return {
            "controller": "ready",
            "autostartEnabled": is_enabled(),
            "missingFiles": provider(manager.configuration.selectedProvider).missing_assets(config),
            "engine": manager.status(),
        }

    @app.get("/api/events")
    async def events():
        return {"events": list(manager.events)}

    async def stream(socket: WebSocket, versioned: bool):
        async with manager.lock:
            selected = provider(manager.configuration.selectedProvider)
            supported = selected.id == "lfm3-audio" if versioned else selected.legacy_audio
            if not supported:
                await socket.accept()
                await socket.close(1003, "The selected model does not support this audio protocol.")
                return
            if manager.state != "ready" or manager.active or manager.reconfiguring:
                await socket.accept()
                await socket.close(1013, "Engine unavailable or another conversation is active.")
                return
            manager.active = True
            revision = manager.configuration.revision
        tasks = []
        try:
            async with connect(
                f"ws://127.0.0.1:{config.worker_port}" + ("/voice/v1/ws" if versioned else "/ws"),
                additional_headers=manager.headers,
                max_size=65536 if versioned else 8192,
                max_queue=4,
                ping_interval=20,
                ping_timeout=20,
                proxy=None,
            ) as remote:
                await socket.accept()

                async def upstream():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        data = message.get("text") if versioned else message.get("bytes")
                        valid = (
                            isinstance(data, str) and len(data.encode()) <= 65536
                            if versioned
                            else data is not None and len(data) == 7680
                        )
                        if not valid:
                            await socket.close(1003, "Invalid audio frame.")
                            return
                        await remote.send(data)

                async def downstream():
                    async for data in remote:
                        if isinstance(data, bytes):
                            if versioned:
                                await socket.close(1003, "Invalid versioned audio event.")
                                return
                            await socket.send_bytes(data)
                        else:
                            if versioned:
                                value = json.loads(data)
                                if value.get("type") == "session.ready":
                                    value["settingsRevision"] = revision
                                    data = json.dumps(value)
                            await socket.send_text(data)

                tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake, WebSocketDisconnect):
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(1011, "Engine connection closed.")
        finally:
            manager.active = False
            for task in tasks:
                task.cancel()
            with anyio.move_on_after(2, shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await socket.close(1000)

    @app.websocket("/ws")
    async def conversation(socket: WebSocket):
        await stream(socket, False)

    @app.websocket("/voice/v1/ws")
    async def audio_check(socket: WebSocket):
        await stream(socket, True)

    @app.exception_handler(HTTPException)
    async def safe_error(_, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": error.detail}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, error):
        from fastapi.responses import JSONResponse

        # Never echo a secret input inside Pydantic's default validation detail.
        return JSONResponse({"error": "Invalid request fields or values."}, status_code=422)

    return app
