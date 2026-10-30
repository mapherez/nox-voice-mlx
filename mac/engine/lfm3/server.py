"""Authenticated loopback audio worker and owner of the private LFM3 runtime."""

import argparse
import asyncio
import hmac
import os
import signal
from contextlib import suppress
from pathlib import Path

import psutil
from controller.profiles import LFM3Settings
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .runtime import LFM3Runtime, validate_settings
from .session import AudioSession


def create_app(runtime, settings: LFM3Settings, token: str):
    validate_settings(settings)
    if len(token) < 32:
        raise ValueError("Start this worker through the authenticated controller.")
    lock = asyncio.Lock()
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )

    def authorized(header):
        return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse

        if not authorized(request.headers.get("authorization", "")):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        memory = psutil.Process(os.getpid()).memory_info().rss
        if runtime.process is not None:
            with suppress(psutil.Error):
                memory += psutil.Process(runtime.process.pid).memory_info().rss
        return {"ready": runtime.ready, "conversationActive": lock.locked(), "memoryBytes": memory}

    @app.put("/settings")
    async def configure(value: LFM3Settings):
        nonlocal settings
        if lock.locked():
            raise HTTPException(409, "End the audio session before changing its settings.")
        try:
            validate_settings(value)
        except ValueError:
            raise HTTPException(422, "Unsupported LFM3 language setting.") from None
        settings = value
        return value.model_dump()

    @app.websocket("/voice/v1/ws")
    async def audio(wire: WebSocket):
        if not authorized(wire.headers.get("authorization", "")):
            await wire.close(1008)
            return
        await wire.accept()
        if lock.locked() or not runtime.ready:
            await wire.close(1013, "The audio worker is busy or unavailable.")
            return
        await lock.acquire()
        session = AudioSession(runtime, settings, wire.send_json)
        try:
            start = await asyncio.wait_for(wire.receive_json(), timeout=10)
            if start != {"type": "session.start", "version": 1}:
                await wire.close(1003, "Unsupported audio session handshake.")
                return
            await session.ready()
            while True:
                value = await wire.receive_json()
                try:
                    await session.handle(value)
                except ValueError:
                    await session.cancel_operation()
                    await session.emit(
                        "error",
                        code="INVALID_CONTROL",
                        message="Invalid audio control message. Start a new turn or reconnect.",
                    )
        except (ValueError, KeyError):
            with suppress(RuntimeError, WebSocketDisconnect):
                await wire.close(1003, "Invalid audio event.")
        except (WebSocketDisconnect, TimeoutError):
            pass
        finally:
            await session.close()
            lock.release()

    return app


async def run(root: Path, settings, token: str, port: int, parent: int):
    import uvicorn

    runtime = LFM3Runtime(root)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for kind in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(kind, stop.set)

    async def parent_watch():
        while os.getppid() == parent:
            await asyncio.sleep(0.5)
        stop.set()

    watcher = asyncio.create_task(parent_watch())
    loading, stopping = asyncio.create_task(runtime.start()), asyncio.create_task(stop.wait())
    server_task = None
    server = None
    try:
        await asyncio.wait({loading, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            return
        await loading
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(runtime, settings, token),
                host="127.0.0.1",
                port=port,
                access_log=False,
                proxy_headers=False,
                ws_max_size=65536,
                ws_max_queue=4,
                timeout_graceful_shutdown=2,
            )
        )
        server_task = asyncio.create_task(server.serve())
        await asyncio.wait({server_task, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            server.should_exit = True
        await server_task
    finally:
        if server is not None:
            server.should_exit = True
        if server_task is not None and not server_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(server_task), timeout=3)
            except (TimeoutError, asyncio.CancelledError):
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
        loading.cancel()
        watcher.cancel()
        stopping.cancel()
        await asyncio.gather(loading, watcher, stopping, return_exceptions=True)
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    parent = int(os.environ.get("NOX_VOICE_CONTROLLER_PID", "0"))
    if parent <= 1 or os.getppid() != parent:
        parser.error("Start this worker through the native controller.")
    root = Path(os.environ["NOX_VOICE_PROVIDER_ASSETS"])
    settings = LFM3Settings.model_validate_json(
        Path(os.environ["NOX_VOICE_ENGINE_SETTINGS"]).read_text()
    )
    asyncio.run(
        run(root, settings, os.environ.get("NOX_VOICE_WORKER_TOKEN", ""), args.port, parent)
    )


if __name__ == "__main__":
    main()
