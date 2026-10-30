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
from .profiles import ProviderId, VoiceConfiguration, load_profiles, save_profiles
from .providers import PROVIDERS, provider


class Supervisor:
    def __init__(self, config: ControllerConfig):
        self.config = config
        self.configuration = load_profiles(config)
        self.process: asyncio.subprocess.Process | None = None
        self.running_provider: ProviderId | None = None
        self.preparation_process = None
        self.preparation = {"provider": None, "state": "idle", "phase": "", "error": None}
        self.worker_memory = None
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
        from .executor.service import ExecutorService
        from .model_library import ModelLibrary

        self.executor = ExecutorService(self)
        self.library = ModelLibrary(self)

    @property
    def settings(self) -> VoiceSettings:
        """The legacy settings API is a projection of the PersonaPlex profile."""
        return self.configuration.profiles.personaplex

    def profiles(self) -> dict:
        return self.configuration.document()

    def providers(self) -> dict:
        return {
            "version": 1,
            "selectedProvider": self.configuration.selectedProvider,
            "providers": [item.describe(self.config) for item in PROVIDERS.values()],
        }

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
            "selectedProvider": self.configuration.selectedProvider,
            "activeProvider": self.running_provider
            if self.process and self.process.returncode is None
            else None,
            "settingsRevision": self.configuration.revision,
            "memoryBytes": self.worker_memory if self.worker_memory is not None else rss,
            "preparation": self.preparation.copy(),
            "modelFilesMissing": provider(self.configuration.selectedProvider).missing_assets(
                self.config
            ),
            "uptimeSeconds": round(time.monotonic() - self.started_at) if self.started_at else 0,
        }

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def start(self) -> dict:
        async with self.lock:
            return await self._start_locked()

    async def _start_locked(self) -> dict:
        selected = provider(self.configuration.selectedProvider)
        if self.library.busy(selected.id):
            raise HTTPException(409, "Wait for this model's download to finish before loading it.")
        if not selected.implemented:
            raise HTTPException(503, selected.reason)
        if self.preparation_process is not None:
            raise HTTPException(
                409, "Wait for model preparation or stop it before starting the engine."
            )
        try:
            selected.validate(self.configuration)
        except ValueError:
            raise HTTPException(
                422, "Choose supported input/output languages for this model."
            ) from None
        if selected.id == "lfm3-audio" and selected.missing_assets(self.config):
            raise HTTPException(503, "Prepare the selected model before starting the engine.")
        if self.state in {"starting", "ready", "stopping"}:
            return self.status()
        if self.process and self.process.returncode is None:
            await self._stop_locked()
        self._token = secrets.token_urlsafe(48)
        self.error = None
        self.worker_memory = None
        self.state, self.phase = "starting", "Starting engine"
        self.active = False
        self.reconfiguring = False
        try:
            save_profiles(self.config, self.configuration)
            write_private(
                selected.worker_settings_file(self.config),
                selected.settings(self.configuration).model_dump_json(),
            )
        except (OSError, ValueError):
            self.state, self.phase, self.error = (
                "error",
                "Error",
                "Could not prepare private engine settings.",
            )
            self.event(self.error)
            return self.status()
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
                "NOX_VOICE_WEIGHTS_DIR": str(selected.assets_dir(self.config).resolve()),
                "NOX_VOICE_ENGINE_SETTINGS": str(
                    selected.worker_settings_file(self.config).resolve()
                ),
                "NOX_VOICE_PROVIDER_ASSETS": str(selected.assets_dir(self.config).resolve()),
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                selected.worker_module,
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
        self.running_provider = selected.id
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
                phases = provider(self.configuration.selectedProvider).phase_labels()
                if self.state == "starting" and phase in phases:
                    self.phase = phases[phase]
                    self.event(self.phase)
            except (ValueError, AttributeError):
                pass  # Never forward raw model output, prompts, paths or tracebacks.

    async def _drain_errors(self, process) -> None:
        while await process.stderr.read(8192):
            pass

    async def _watch_exit(self, process) -> None:
        await process.wait()
        if process is self.process and self.state not in {"stopping", "stopped"}:
            await self.executor.end_all()
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
        await self.executor.end_all()
        preparing = self.preparation_process
        if preparing is not None:
            self.preparation_process = None
            self.preparation.update(state="cancelled", phase="Preparation stopped", error=None)
            if preparing.returncode is None:
                preparing.terminate()
                try:
                    await asyncio.wait_for(preparing.wait(), timeout=5)
                except TimeoutError:
                    preparing.kill()
                    await preparing.wait()
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
        self.running_provider = None
        self.worker_memory = None
        self.state, self.phase, self.error, self.active = "stopped", "Stopped", None, False
        self.event("Engine stopped")
        return self.status()

    async def restart(self) -> dict:
        async with self.lock:
            await self._stop_locked()
            return await self._start_locked()

    async def update_settings(self, settings: VoiceSettings) -> dict:
        await self._update_configuration(legacy=settings)
        return self.settings.model_dump()

    async def update_profiles(self, value: VoiceConfiguration) -> dict:
        await self._update_configuration(document=value)
        return self.profiles()

    def _save_configuration(self, value: VoiceConfiguration) -> None:
        try:
            save_profiles(self.config, value)
        except (OSError, ValueError):
            raise HTTPException(
                500, "Could not save voice settings. No new profile was applied."
            ) from None
        self.configuration = value

    async def _update_configuration(
        self, *, legacy: VoiceSettings | None = None, document: VoiceConfiguration | None = None
    ) -> None:
        async with self.lock:
            if document is not None and document.revision != self.configuration.revision:
                raise HTTPException(
                    409, "Voice settings changed. Reload Settings before applying your changes."
                )
            proposed = (document or self.configuration).model_copy(deep=True)
            if legacy is not None:
                proposed.profiles.personaplex = legacy.model_copy(deep=True)
            selected = provider(proposed.selectedProvider)
            if not selected.implemented:
                raise HTTPException(503, selected.reason)
            try:
                for item in PROVIDERS.values():
                    if item.id == proposed.selectedProvider or item.settings(
                        proposed
                    ) != item.settings(self.configuration):
                        item.validate(proposed)
            except ValueError:
                raise HTTPException(
                    422, "Choose supported input/output languages for this model."
                ) from None
            provider_changed = proposed.selectedProvider != self.configuration.selectedProvider
            active_changed = provider_changed or selected.settings(proposed) != selected.settings(
                self.configuration
            )
            if (
                self.reconfiguring
                or self.preparation_process is not None
                or active_changed
                and (self.active or self.state in {"starting", "stopping"})
            ):
                raise HTTPException(409, "End the conversation before changing engine settings.")
            if proposed == self.configuration:
                return
            proposed.revision = self.configuration.revision + 1
            if provider_changed and (
                self.state == "ready" or self.process and self.process.returncode is None
            ):
                await self._stop_locked()
                try:
                    self._save_configuration(proposed)
                except HTTPException:
                    self.state, self.phase, self.error = (
                        "error",
                        "Error",
                        "Could not save the model selection.",
                    )
                    raise
                self.event("Voice model selected")
                return
            if self.state != "ready" or not active_changed:
                self._save_configuration(proposed)
                self.event("Voice settings updated")
                return
            process = self.process
            headers = self.headers
            self.reconfiguring = True
            self.phase = "Applying voice settings"
        try:
            # Stop can terminate the worker while a long reconfiguration is pending.
            response = await self.client.put(
                "/settings",
                json=selected.settings(proposed).model_dump(),
                headers=headers,
                timeout=180,
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
                try:
                    self._save_configuration(proposed)
                except HTTPException:
                    self.state, self.phase, self.error = (
                        "error",
                        "Error",
                        "Could not persist engine settings. Restart the engine.",
                    )
                    raise
                self.phase = "Ready"
                self.event("Voice settings updated")
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
                memory = response.json().get("memoryBytes") if response.status_code == 200 else None
                if process is self.process and type(memory) is int and memory >= 0:
                    self.worker_memory = memory
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

    async def prepare_provider(self, provider_id: ProviderId) -> dict:
        async with self.lock:
            selected = provider(provider_id)
            if selected.id != "lfm3-audio":
                raise HTTPException(
                    422, "Use the existing model preparation command for this provider."
                )
            if (
                self.active
                or self.reconfiguring
                or self.state in {"starting", "stopping"}
                or self.preparation_process is not None
            ):
                raise HTTPException(
                    409,
                    "End the conversation or wait for the current operation before preparing assets.",
                )
            if (
                self.running_provider == provider_id
                and self.process
                and self.process.returncode is None
            ):
                raise HTTPException(409, "Stop this model before preparing its assets.")
            environment = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"}
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "engine.lfm3.assets",
                    "--root",
                    str(selected.assets_dir(self.config).resolve()),
                    cwd=MAC_ROOT,
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError:
                raise HTTPException(500, "Could not start model preparation.") from None
            self.preparation_process = process
            self.preparation = {
                "provider": provider_id,
                "state": "preparing",
                "phase": "Preparing model files",
                "error": None,
            }
            self._task(self._read_preparation(process))
            self._task(self._drain_errors(process))
            self._task(self._watch_preparation(process))
            return self.status()

    async def _read_preparation(self, process):
        phases = {
            "runtime": "Preparing verified runtime",
            "models": "Preparing model weights",
            "ready": "Verifying preparation",
        }
        async for line in process.stdout:
            if process is not self.preparation_process:
                return
            try:
                phase = phases.get(json.loads(line).get("phase"))
                if phase:
                    self.preparation["phase"] = phase
            except (ValueError, AttributeError):
                pass

    async def _watch_preparation(self, process):
        code = await process.wait()
        if process is self.preparation_process:
            self.preparation_process = None
            self.preparation.update(
                state="ready" if code == 0 else "error",
                phase="Model files prepared" if code == 0 else "Preparation failed",
                error=None
                if code == 0
                else "Model preparation failed. Check disk space and network access, then retry.",
            )
            self.event("Model preparation completed" if code == 0 else "Model preparation failed")

    async def close(self) -> None:
        try:
            await self.executor.close()
            await self.library.close()
        finally:
            await self.stop()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.client.aclose()
