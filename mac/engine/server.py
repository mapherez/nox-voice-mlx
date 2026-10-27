"""Private loopback worker. PCM and sampling match the original MLX implementation."""

import argparse
import asyncio
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from controller.config import VoiceSettings
from controller.model_files import verify_model
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect


def create_app() -> FastAPI:
    token = os.environ.get("NOX_VOICE_WORKER_TOKEN", "")
    if len(token) < 32:
        raise ValueError("The engine must be started by the authenticated controller.")
    lock = asyncio.Lock()
    settings = VoiceSettings.model_validate_json(
        Path(os.environ["NOX_VOICE_ENGINE_SETTINGS"]).read_text()
    )
    inference = None
    ready = False

    @asynccontextmanager
    async def lifespan(_):
        nonlocal inference, ready
        print(json.dumps({"phase": "verifying"}), flush=True)
        verify_model(Path(os.environ["NOX_VOICE_WEIGHTS_DIR"]))
        from . import inference as implementation

        inference = implementation
        inference.load_model(settings)
        ready = True
        yield
        ready = False

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )

    def authorized(header: str) -> bool:
        return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        from fastapi.responses import JSONResponse

        if not authorized(request.headers.get("authorization", "")):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"ready": ready, "conversationActive": lock.locked()}

    @app.put("/settings")
    async def update(value: VoiceSettings):
        nonlocal settings, ready
        if lock.locked():
            raise HTTPException(409, "Engine is busy.")
        async with lock:
            ready = False
            try:
                if value.bits != settings.bits:
                    inference.MODEL = None
                    inference.MIMI = None
                    inference.VOICE_CACHE_STATE = None
                    inference.mx.clear_cache()
                    inference.load_model(value)
                else:
                    inference.load_voice_prompt(value.voice, value.prompt)
                settings = value
                ready = True
            except Exception:
                raise HTTPException(
                    500, "Model reconfiguration failed. Restart the engine."
                ) from None
        return value.model_dump()

    @app.websocket("/ws")
    async def conversation(socket: WebSocket):
        nonlocal ready
        if not authorized(socket.headers.get("authorization", "")):
            await socket.close(1008)
            return
        if not ready or lock.locked():
            await socket.accept()
            await socket.close(1013, "Engine is busy.")
            return
        await lock.acquire()  # Restore state only AFTER reserving exclusive ownership.
        try:
            import numpy as np
            from moshi_mlx.utils import sampling

            mx = inference.mx
            inference.MIMI.reset()
            inference.restore_cache_state()
            text_sampler = sampling.Sampler(temp=0.7, top_k=25)
            audio_sampler = sampling.Sampler(temp=0.8, top_k=250)
            text_token = mx.array([[inference.CONFIG.text_out_vocab_size]])
            previous = mx.array(inference.SILENCE_TOKENS.reshape(1, 8, 1), dtype=mx.int32)
            frames, generation_time = 0, 0.0
            await socket.accept()
            while True:
                data = await asyncio.wait_for(socket.receive_bytes(), timeout=30)
                if len(data) != 7680:
                    await socket.close(1003, "Invalid audio frame.")
                    return
                audio = np.frombuffer(data, dtype=np.float32)
                if not np.isfinite(audio).all():
                    await socket.close(1003, "Invalid audio samples.")
                    return
                encoded = inference.MIMI.encode_step(audio[None, None, :])
                if encoded is None:
                    continue
                user_tokens = mx.array(encoded.astype(np.int64))
                all_audio = [previous[:, cb, :] for cb in range(8)] + [
                    user_tokens[:, cb, :] for cb in range(8)
                ]
                started = time.perf_counter()
                next_text, next_audio = inference.MODEL.sample(
                    text_token, all_audio, text_sampler, audio_sampler
                )
                mx.eval(next_text, next_audio)
                generation_time += time.perf_counter() - started
                previous = next_audio[:, :8, :]
                pcm = inference.MIMI.decode_step(np.array(previous).astype(np.uint32))
                if pcm is not None:
                    await socket.send_bytes(pcm[0, 0].astype(np.float32).tobytes())
                text_token = next_text
                frames += 1
                if frames % 10 == 0:
                    await socket.send_text(
                        json.dumps(
                            {
                                "type": "stats",
                                "frames": frames,
                                "avg_latency_ms": round(generation_time / frames * 1000, 1),
                                "rtf": round((frames / 12.5) / max(generation_time, 0.0001), 2),
                            }
                        )
                    )
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            ready = False
            try:
                await socket.close(1011, "Audio processing failed. Restart the conversation.")
            except RuntimeError:
                pass
        finally:
            try:
                if inference is not None:
                    inference.MIMI.reset()
                    inference.restore_cache_state()
            except Exception:
                ready = False
            finally:
                lock.release()

    return app


def main():
    parser = argparse.ArgumentParser(description="Private NoX Voice engine")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    parent = int(os.environ.get("NOX_VOICE_CONTROLLER_PID", "0"))
    if parent <= 1 or os.getppid() != parent:
        parser.error("Start the engine through the native controller.")

    def watch_parent():
        while True:
            time.sleep(3)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    import uvicorn

    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        proxy_headers=False,
        ws_max_size=8192,
        ws_max_queue=4,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
