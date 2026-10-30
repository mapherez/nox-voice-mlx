"""Loopback-only visual QA fixture, NOT an authentication mode or deployable app.

Serves the compiled frontend with synthetic API responses. No model is loaded,
no Google account is contacted, and no audio endpoint is provided.
"""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from controller.config import ControllerConfig
from controller.profiles import ConfigurationUpdate, VoiceConfiguration
from controller.providers import PROVIDERS

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dist"
STATE = {
    "state": "stopped",
    "phase": "Stopped",
    "error": None,
    "conversationActive": False,
    "settingsChanging": False,
    "memoryBytes": 0,
    "controllerReachable": True,
    "selectedProvider": "personaplex",
    "activeProvider": None,
    "modelFilesMissing": [],
    "preparation": {"provider": None, "state": "idle", "phase": "", "error": None},
}
SETTINGS = {
    "voice": "NATF0",
    "prompt": "You are a helpful AI assistant. Be concise and friendly.",
    "bits": 4,
}
PROFILES = VoiceConfiguration().document()
PREVIEW_CONFIG = ControllerConfig(
    data_dir=ROOT.parents[1] / ".tmp" / "preview",
    weights_dir=ROOT.parents[1] / ".tmp" / "preview" / "weights",
)
MODELS = {key: {"id": key, "name": adapter.name, "runtime": adapter.runtime,
    "source": f"https://huggingface.co/{adapter.model_id}", "revision": "a" * 40,
    "downloadBytes": 2_433_548_178 if key == "lfm3-audio" else 17_200_000_000,
    "sizeApproximate": key == "personaplex", "credentialService": "huggingface" if key == "personaplex" else None,
    "localState": "present" if key == "personaplex" else "not_downloaded", "loaded": False}
    for key, adapter in PROVIDERS.items()}
JOBS = []
CREDENTIAL = {"id": "huggingface", "name": "Hugging Face", "fieldLabel": "Access token",
    "help": "Read access is sufficient. Accept PersonaPlex access terms before downloading.",
    "tokenUrl": "https://huggingface.co/settings/tokens", "accessUrl": "https://huggingface.co/nvidia/personaplex-7b-v1",
    "configured": False, "test": None}


def fixture_download(provider_id):
    job = {"id": f"{len(JOBS) + 1:024x}", "provider": provider_id, "name": MODELS[provider_id]["name"],
        "state": "downloading", "files": [{"name": "fixture-model.gguf", "totalBytes": 1000, "downloadedBytes": 250, "state": "downloading"}],
        "downloadedBytes": 250, "totalBytes": 1000, "bytesPerSecond": 300, "error": None}
    JOBS.append(job)

    def complete():
        if job["state"] != "downloading":
            return
        job.update(state="completed", downloadedBytes=1000, bytesPerSecond=0)
        job["files"][0].update(state="downloaded", downloadedBytes=1000)
        MODELS[provider_id]["localState"] = "verified"
        if STATE["selectedProvider"] == provider_id:
            STATE["modelFilesMissing"] = []

    threading.Timer(3, complete).start()
    return job


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_):
        pass

    def json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        responses = {
            "/api/session": {
                "email": "preview@example.test",
                "csrfToken": "fixture-only",
                "expiresAt": time.time() + 3600,
                "controllerUrl": "https://controller.example.test:8443",
            },
            "/api/engine/status": STATE,
            "/api/settings": SETTINGS,
            "/api/voice/v1/providers": {
                "version": 1,
                "selectedProvider": PROFILES["selectedProvider"],
                "providers": [provider.describe(PREVIEW_CONFIG) for provider in PROVIDERS.values()],
            },
            "/api/voice/v1/settings": PROFILES,
            "/api/voice/v1/models": {"models": list(MODELS.values())},
            "/api/voice/v1/downloads": {"jobs": JOBS},
            "/api/voice/v1/credentials": {"services": [CREDENTIAL]},
            "/api/diagnostics": {
                "controllerUrl": "https://controller.example.test:8443",
                "autostartEnabled": False,
                "missingFiles": [],
            },
            "/api/events": {
                "events": [
                    {
                        "time": time.time(),
                        "message": "Visual QA fixture — no real controller connected",
                    }
                ]
            },
        }
        if self.path in responses:
            self.json(responses[self.path])
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.endswith("/open-folder"):
            self.json({"message": "Folder opened in Finder on the controller Mac."})
            return
        if self.path == "/api/voice/v1/credentials/huggingface/test":
            CREDENTIAL["test"] = {"state": "valid", "message": "Token valid. PersonaPlex download access confirmed."}
            self.json(CREDENTIAL["test"])
            return
        if self.path == "/api/voice/v1/downloads":
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.json(fixture_download(value["provider"]), 202)
            return
        if self.path.startswith("/api/voice/v1/downloads/"):
            key, action = self.path.split("/")[-2:]
            job = next(item for item in JOBS if item["id"] == key)
            if action == "retry":
                self.json(fixture_download(job["provider"]), 202)
            else:
                job.update(state="cancelled", bytesPerSecond=0)
                self.json(job)
            return
        if self.path == "/api/voice/v1/providers/lfm3-audio/prepare":
            STATE["preparation"] = {
                "provider": "lfm3-audio",
                "state": "preparing",
                "phase": "Preparing model weights (visual QA fixture)",
                "error": None,
            }

            def prepared():
                STATE["preparation"].update(state="ready", phase="Model files prepared")
                STATE["modelFilesMissing"] = []

            threading.Timer(0.2, prepared).start()
            self.json(STATE, 202)
            return
        if self.path not in {
            prefix + action
            for prefix in ("/api/engine/", "/api/voice/v1/engine/")
            for action in ("start", "stop", "restart", "load", "eject")
        }:
            self.send_error(404)
            return
        if self.path.endswith(("stop", "eject")):
            STATE.update(state="stopped", phase="Stopped", memoryBytes=0, activeProvider=None)
            for model in MODELS.values():
                model["loaded"] = False
        else:
            STATE.update(state="starting", phase="Loading model (visual QA fixture)")

            def ready():
                STATE.update(
                    state="ready",
                    phase="Ready",
                    memoryBytes=4_500_000_000,
                    activeProvider=PROFILES["selectedProvider"],
                )
                MODELS[PROFILES["selectedProvider"]]["loaded"] = True

            threading.Timer(2, ready).start()
        self.json(STATE)

    def do_PUT(self):
        if self.path == "/api/voice/v1/credentials/huggingface":
            self.rfile.read(int(self.headers["Content-Length"]))  # Never retain even fixture tokens.
            CREDENTIAL.update(configured=True, test=None)
            self.json({"services": [CREDENTIAL]})
            return
        if self.path == "/api/voice/v1/settings":
            value = ConfigurationUpdate.model_validate_json(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            if value.revision != PROFILES["revision"]:
                self.json(
                    {"error": "Settings changed. Reload Settings before applying your changes."},
                    409,
                )
                return
            if PROFILES["selectedProvider"] != value.selectedProvider:
                STATE.update(state="stopped", phase="Stopped", activeProvider=None)
                STATE["modelFilesMissing"] = (
                    ["fixture-model"] if MODELS[value.selectedProvider]["localState"] == "not_downloaded" else []
                )
            PROFILES.update(value.document())
            STATE["selectedProvider"] = PROFILES["selectedProvider"]
            PROFILES["revision"] += 1
            SETTINGS.update(PROFILES["profiles"]["personaplex"])
            self.json(PROFILES)
            return
        if self.path != "/api/settings":
            self.send_error(404)
            return
        SETTINGS.update(
            json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 32768)))
        )
        self.json(SETTINGS)

    def do_DELETE(self):
        if self.path == "/api/voice/v1/downloads":
            JOBS[:] = [job for job in JOBS if job["state"] in {"queued", "resolving", "downloading", "verifying", "extracting", "cancelling"}]
            self.json({"jobs": JOBS})
        elif self.path == "/api/voice/v1/credentials/huggingface":
            CREDENTIAL.update(configured=False, test=None)
            self.json({"services": [CREDENTIAL]})
        else:
            self.send_error(404)


if __name__ == "__main__":
    print(
        "Visual QA fixture: http://127.0.0.1:18765 — synthetic state, no engine or audio",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", 18765), Preview).serve_forever()
