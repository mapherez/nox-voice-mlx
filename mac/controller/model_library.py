"""Controller-owned download jobs, asset metadata and native folder actions."""

import asyncio
import json
import math
import os
import secrets
import sys
import time
from contextlib import suppress

from engine.lfm3 import assets
from fastapi import HTTPException

from .config import MAC_ROOT, write_private
from .credentials import Credentials
from .model_files import artifact_manifest
from .providers import PROVIDERS, provider

ACTIVE = {"queued", "resolving", "downloading", "verifying", "extracting", "cancelling"}


class ModelLibrary:
    def __init__(self, manager):
        self.manager, self.config = manager, manager.config
        self.credentials = Credentials(self.config)
        self.path = self.config.data_dir / "downloads.json"
        self.jobs, self.tasks, self.processes, self.verified = {}, {}, {}, {}
        self.closed = False
        self.last_save = 0
        if self.path.is_file():
            for job in json.loads(self.path.read_text()).get("jobs", [])[-20:]:
                if job.get("provider") not in PROVIDERS:
                    continue
                if job.get("state") in ACTIVE:
                    job.update(state="interrupted", bytesPerSecond=0,
                        error="Controller stopped. Retry to resume the download.")
                self.jobs[job["id"]] = job

    def save(self, force=False):
        if force or time.monotonic() - self.last_save > 1:
            write_private(self.path, json.dumps(self.snapshot()))
            self.last_save = time.monotonic()

    def snapshot(self):
        return {"jobs": list(self.jobs.values())}

    def busy(self, provider_id):
        return any(job["provider"] == provider_id and job["state"] in ACTIVE for job in self.jobs.values())

    def signature(self, adapter):
        root = adapter.assets_dir(self.config)
        if not root.exists():
            return ()
        files = [root / "installed.json", *list((root / "models").glob("*.gguf")),
            *list((root / "runtime").rglob("*"))] if adapter.id == "lfm3-audio" else [
            root / "manifest.json", *list(root.glob("*.safetensors")),
            root / "voices.tgz", root / "tokenizer_spm_32k_3.model", *list((root / "voices_cpu").glob("*.pt"))]
        return tuple((str(file), file.stat().st_size, file.stat().st_mtime_ns) for file in files if file.is_file())

    def models(self):
        values = []
        for adapter in PROVIDERS.values():
            root = adapter.assets_dir(self.config)
            missing = adapter.missing_assets(self.config)
            data = artifact_manifest()
            revision, total, approximate = data["revision"], sum(item["size"] for item in data["files"]), False
            if adapter.id == "lfm3-audio":
                manifest = assets.manifest()
                revision = manifest["model"]["revision"]
                total = manifest["runtime"]["size"] + sum(item["size"] for item in manifest["model"]["files"])
                approximate = False
            else:
                with suppress(OSError, ValueError, KeyError, TypeError):
                    data = json.loads((root / ".download-plan.json").read_text())
                    revision = data["revision"]
                    total = sum(item["size"] for item in data["files"])
                    approximate = False
                with suppress(OSError, ValueError):
                    revision = json.loads((root / "manifest.json").read_text()).get("revision") or revision
            verified = not missing and (
                self.verified.get(adapter.id) == self.signature(adapter)
                or self.manager.running_provider == adapter.id and self.manager.state == "ready")
            values.append({"id": adapter.id, "name": adapter.name, "source": f"https://huggingface.co/{adapter.model_id}",
                "runtime": adapter.runtime, "revision": revision, "directory": str(root.resolve()),
                "downloadBytes": total, "sizeApproximate": approximate,
                "credentialService": "huggingface" if adapter.id == "personaplex" else None,
                "localState": "verified" if verified else "present" if not missing else "not_downloaded",
                "loaded": self.manager.running_provider == adapter.id and self.manager.state == "ready",
                "missingCount": len(missing)})
        return {"models": values}

    async def download(self, provider_id):
        if self.closed:
            raise HTTPException(503, "The controller is shutting down.")
        adapter = provider(provider_id)
        if not adapter.assets_dir(self.config).resolve().is_relative_to(self.config.weights_dir.resolve()):
            raise HTTPException(422, "The model directory must stay inside the configured weights root.")
        if self.busy(provider_id):
            return next(job for job in self.jobs.values() if job["provider"] == provider_id and job["state"] in ACTIVE)
        if self.manager.running_provider == provider_id and self.manager.process and self.manager.process.returncode is None:
            raise HTTPException(409, "Eject this model before downloading or repairing its files.")
        if self.manager.preparation_process is not None:
            raise HTTPException(409, "Wait for the current model preparation to finish.")
        if provider_id == "personaplex" and not self.credentials.secret("huggingface"):
            raise HTTPException(422, "Add a Hugging Face token in Credentials before downloading PersonaPlex.")
        while len(self.jobs) >= 20:
            oldest = next((key for key, item in self.jobs.items() if item["state"] not in ACTIVE), None)
            if oldest is None:
                raise HTTPException(409, "The download queue is full.")
            del self.jobs[oldest]
        job = {"id": secrets.token_hex(12), "provider": provider_id, "name": adapter.name,
            "state": "queued", "createdAt": time.time(), "files": [], "downloadedBytes": 0,
            "totalBytes": 0, "bytesPerSecond": 0, "error": None, "code": None}
        self.jobs[job["id"]] = job
        self.verified.pop(provider_id, None)
        self.save(True)
        self.tasks[job["id"]] = asyncio.create_task(self.run(job))
        return job

    async def run(self, job):
        key = job["id"]
        process = None
        try:
            job["state"] = "resolving"
            adapter = provider(job["provider"])
            token = self.credentials.secret("huggingface") if adapter.id == "personaplex" else None
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "controller.download_worker",
                "--provider", adapter.id, "--root", str(adapter.assets_dir(self.config).resolve()), cwd=MAC_ROOT,
                env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536)
            self.processes[key] = process
            process.stdin.write(json.dumps({"token": token}).encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            complete = False
            async for line in process.stdout:
                try:
                    event = json.loads(line)
                    phase = event.get("phase")
                    if phase in {"downloading", "verifying", "extracting"}:
                        job["state"] = phase
                    if phase == "complete":
                        complete = True
                    if phase == "error":
                        job.update(error=str(event.get("error", "Download failed."))[:500], code=event.get("code"))
                    for field in ("downloadedBytes", "totalBytes", "bytesPerSecond"):
                        value = event.get(field)
                        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                            job[field] = value
                    if isinstance(event.get("files"), list) and len(event["files"]) <= 8:
                        job["files"] = event["files"]
                    self.save()
                except (ValueError, TypeError):
                    pass  # Never relay raw subprocess output.
            code = await process.wait()
            if code == 0 and complete:
                self.verified[adapter.id] = self.signature(adapter)
                job.update(state="completed", bytesPerSecond=0)
            else:
                job.update(state="failed", bytesPerSecond=0,
                    error=job["error"] or "Download interrupted. Retry to resume it.")
        except asyncio.CancelledError:
            job.update(state="interrupted" if self.closed else "cancelled", bytesPerSecond=0,
                error="Controller stopped. Retry to resume." if self.closed else None)
            raise
        except (OSError, ValueError, RuntimeError):
            job.update(state="failed", bytesPerSecond=0, error="Could not prepare the model. Check the native installation and retry.")
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            self.processes.pop(key, None)
            self.tasks.pop(key, None)
            self.save(True)

    async def cancel(self, key):
        if key not in self.jobs:
            raise HTTPException(404, "Download not found.")
        task = self.tasks.get(key)
        if task:
            self.jobs[key]["state"] = "cancelling"
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Also handles a job cancelled before its coroutine first runs.
            if self.jobs[key]["state"] == "cancelling":
                self.jobs[key]["state"] = "cancelled"
            self.tasks.pop(key, None)
            self.save(True)
        return self.jobs[key]

    async def retry(self, key):
        if key not in self.jobs:
            raise HTTPException(404, "Download not found.")
        if self.jobs[key]["state"] in ACTIVE:
            raise HTTPException(409, "This download is still active.")
        return await self.download(self.jobs[key]["provider"])

    async def open_folder(self, provider_id):
        root = provider(provider_id).assets_dir(self.config)
        if not root.resolve().is_relative_to(self.config.weights_dir.resolve()):
            raise HTTPException(422, "The model folder must stay inside the configured weights directory.")
        if sys.platform != "darwin" or os.stat("/dev/console").st_uid != os.getuid() or os.getuid() == 0:
            raise HTTPException(409, "Finder requires a graphical login on the controller Mac. Sign in on that Mac and try again.")
        root.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec("/usr/bin/open", "-a", "Finder", str(root.resolve()),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()
            code = 1
        if code:
            raise HTTPException(409, "Finder could not open the folder. Check the graphical login on the controller Mac.")
        return {"message": "Folder opened in Finder on the controller Mac."}

    async def close(self):
        self.closed = True
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self.tasks.clear()
        for job in self.jobs.values():
            if job["state"] in ACTIVE:
                job.update(state="interrupted", bytesPerSecond=0, error="Controller stopped. Retry to resume.")
        if self.jobs:
            self.save(True)
