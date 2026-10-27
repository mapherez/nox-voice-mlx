"""Own exactly one engine child process; remain responsive while it loads."""

import asyncio
import json
import os
import secrets
import sys
import time
from collections import deque
from contextlib import suppress

import httpx2 as httpx
import psutil
from fastapi import HTTPException

from .config import MAC_ROOT, ControllerConfig, VoiceSettings, write_private

PHASES = {
    "verifying": "Verifying model files",
    "loading": "Loading PersonaPlex 7B",
    "quantizing": "Applying quantization",
    "codec": "Loading audio codec",
    "warming": "Warming up the model",
    "voice": "Preparing voice",
    "ready": "Ready",
}


class Supervisor:
    def __init__(self, config: ControllerConfig):
        self.config = config
        self.settings = config.load_settings()
        self.process: asyncio.subprocess.Process | None = None
        self.state = "stopped"
        self.phase = "Stopped"
        self.error: str | None = None
        self.active = False
        self.reconfiguring = False
        self.started_at: float | None = None
        self.events: deque[dict] = deque(maxlen=200)
        self.lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._token = ""
        self.client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{config.worker_port}", timeout=5, trust_env=False
        )

    def event(self, message: str) -> None:
        self.events.append({"time": time.time(), "message": message})

    def _task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def status(self) -> dict:
        rss = 0
        if self.process and self.process.returncode is None:
            with suppress(psutil.Error):
                rss = psutil.Process(self.process.pid).memory_info().rss
        return {
            "state": self.state,
            "phase": self.phase,
            "error": self.error,
            "conversationActive": self.active,
            "settingsChanging": self.reconfiguring,
            "memoryBytes": rss,
            "uptimeSeconds": round(time.monotonic() - self.started_at) if self.started_at else 0,
        }

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def start(self) -> dict:
        async with self.lock:
            return await self._start_locked()

    async def _start_locked(self) -> dict:
        if self.state in {"starting", "ready", "stopping"}:
            return self.status()
        if self.process and self.process.returncode is None:
            await self._stop_locked()
        self._token = secrets.token_urlsafe(48)
        self.error = None
        self.state, self.phase = "starting", "Starting engine"
        self.active = False
        self.reconfiguring = False
        write_private(self.config.settings_file, self.settings.model_dump_json())
        environment = os.environ.copy()
        for key in list(environment):
            if any(
                word in key.upper()
                for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")
            ):
                environment.pop(key, None)
        environment.update(
            {
                "NOX_VOICE_WORKER_TOKEN": self._token,
                "NOX_VOICE_CONTROLLER_PID": str(os.getpid()),
                "NOX_VOICE_WEIGHTS_DIR": str(self.config.weights_dir.resolve()),
                "NOX_VOICE_ENGINE_SETTINGS": str(self.config.settings_file.resolve()),
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "engine.server",
                "--port",
                str(self.config.worker_port),
                cwd=MAC_ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=32768,
            )
        except OSError:
            self.state, self.phase, self.error = (
                "error",
                "Error",
                "Could not start the engine process.",
            )
            self.event(self.error)
            return self.status()
        self.started_at = time.monotonic()
        self.event("Engine starting")
        process = self.process
        self._task(self._read_output(process))
        self._task(self._drain_errors(process))
        self._task(self._watch_exit(process))
        self._task(self._wait_ready(process))
        return self.status()

    async def _read_output(self, process) -> None:
        async for line in process.stdout:
            if process is not self.process:
                return
            try:
                payload = json.loads(line)
                phase = payload.get("phase")
                if self.state == "starting" and phase in PHASES:
                    self.phase = PHASES[phase]
                    self.event(self.phase)
            except (ValueError, AttributeError):
                pass  # Never forward raw model output, prompts, paths or tracebacks.

    async def _drain_errors(self, process) -> None:
        while await process.stderr.read(8192):
            pass

    async def _watch_exit(self, process) -> None:
        await process.wait()
        if process is self.process and self.state not in {"stopping", "stopped"}:
            self.state, self.phase, self.active = "error", "Error", False
            self.error = (
                "Engine exited. Check model files and the native installation, then restart."
            )
            self.event(self.error)

    async def _wait_ready(self, process) -> None:
        deadline = time.monotonic() + self.config.startup_timeout
        while process is self.process and self.state == "starting" and process.returncode is None:
            try:
                response = await self.client.get("/health", headers=self.headers, timeout=1)
                if response.status_code == 200 and response.json().get("ready") is True:
                    if process is self.process and self.state == "starting":
                        self.state, self.phase = "ready", "Ready"
                        self.event("Engine ready")
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if time.monotonic() >= deadline:
                async with self.lock:
                    if process is self.process and self.state == "starting":
                        await self._stop_locked()
                        self.state, self.phase, self.error = (
                            "error",
                            "Error",
                            "Engine startup timed out.",
                        )
                        self.event(self.error)
                return
            await asyncio.sleep(0.5)

    async def stop(self) -> dict:
        async with self.lock:
            return await self._stop_locked()

    async def _stop_locked(self) -> dict:
        process = self.process
        self.state, self.phase = "stopping", "Stopping engine"
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        self.process, self.started_at = None, None
        self.state, self.phase, self.error, self.active = "stopped", "Stopped", None, False
        self.event("Engine stopped")
        return self.status()

    async def restart(self) -> dict:
        async with self.lock:
            await self._stop_locked()
            return await self._start_locked()

    async def update_settings(self, settings: VoiceSettings) -> dict:
        async with self.lock:
            if self.active or self.reconfiguring or self.state in {"starting", "stopping"}:
                raise HTTPException(409, "End the conversation before changing engine settings.")
            if self.state != "ready":
                self.settings = settings
                write_private(self.config.settings_file, settings.model_dump_json())
                self.event("Voice settings updated")
                return settings.model_dump()
            process = self.process
            headers = self.headers
            self.reconfiguring = True
            self.phase = "Applying voice settings"
        try:
            # Stop can terminate the worker while a long reconfiguration is pending.
            response = await self.client.put(
                "/settings", json=settings.model_dump(), headers=headers, timeout=180
            )
            async with self.lock:
                if process is not self.process or self.state != "ready":
                    raise HTTPException(
                        409, "Settings change cancelled because the engine stopped."
                    )
                if response.status_code != 200:
                    self.state, self.phase, self.error = (
                        "error",
                        "Error",
                        "Could not reconfigure the engine. Restart it.",
                    )
                    raise HTTPException(502, self.error)
                self.settings = settings
                write_private(self.config.settings_file, settings.model_dump_json())
                self.phase = "Ready"
                self.event("Voice settings updated")
                return settings.model_dump()
        except httpx.HTTPError:
            if process is self.process and self.state == "ready":
                self.state, self.phase, self.error = (
                    "error",
                    "Error",
                    "Engine settings request failed. Restart the engine.",
                )
            raise HTTPException(502, "Engine settings request failed or was cancelled.") from None
        finally:
            self.reconfiguring = False

    async def refresh_status(self) -> dict:
        if self.state == "ready" and not self.reconfiguring:
            process = self.process
            try:
                response = await self.client.get("/health", headers=self.headers, timeout=2)
                if response.status_code == 200 and response.json().get("ready") is not True:
                    if process is self.process and self.state == "ready":
                        self.state, self.phase, self.error = (
                            "error",
                            "Error",
                            "Engine is not ready. Restart it to recover.",
                        )
            except (httpx.HTTPError, ValueError):
                pass  # Briefly busy workers may miss health checks; exit is watched separately.
        return self.status()

    async def close(self) -> None:
        await self.stop()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
