"""mTLS controller API. Run using the TLS-enforcing CLI, never bare uvicorn."""

import asyncio
from contextlib import asynccontextmanager, suppress

import anyio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .config import ControllerConfig, VoiceSettings
from .supervisor import Supervisor


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

    @app.get("/api/diagnostics")
    async def diagnostics():
        from .autostart import is_enabled
        from .model_files import missing_files

        return {
            "controller": "ready",
            "autostartEnabled": is_enabled(),
            "missingFiles": missing_files(config.weights_dir),
            "engine": manager.status(),
        }

    @app.get("/api/events")
    async def events():
        return {"events": list(manager.events)}

    @app.websocket("/ws")
    async def conversation(socket: WebSocket):
        async with manager.lock:
            if manager.state != "ready" or manager.active or manager.reconfiguring:
                await socket.accept()
                await socket.close(1013, "Engine unavailable or another conversation is active.")
                return
            manager.active = True
        tasks = []
        try:
            async with connect(
                f"ws://127.0.0.1:{config.worker_port}/ws",
                additional_headers=manager.headers,
                max_size=8192,
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
                        data = message.get("bytes")
                        if data is None or len(data) != 7680:
                            await socket.close(1003, "Invalid audio frame.")
                            return
                        await remote.send(data)

                async def downstream():
                    async for data in remote:
                        if isinstance(data, bytes):
                            await socket.send_bytes(data)
                        else:
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

    @app.exception_handler(HTTPException)
    async def safe_error(_, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": error.detail}, status_code=error.status_code)

    return app
