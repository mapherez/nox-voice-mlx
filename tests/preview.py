"""Loopback-only visual QA fixture, NOT an authentication mode or deployable app.

Serves the compiled frontend with synthetic API responses. No model is loaded,
no Google account is contacted, and no audio endpoint is provided.
"""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "dist"
STATE = {
    "state": "stopped",
    "phase": "Stopped",
    "error": None,
    "conversationActive": False,
    "settingsChanging": False,
    "memoryBytes": 0,
    "controllerReachable": True,
}
SETTINGS = {
    "voice": "NATF0",
    "prompt": "You are a helpful AI assistant. Be concise and friendly.",
    "bits": 4,
}


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_):
        pass

    def json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
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
                "controllerUrl": "https://192.168.1.50:8443",
            },
            "/api/engine/status": STATE,
            "/api/settings": SETTINGS,
            "/api/diagnostics": {
                "controllerUrl": "https://192.168.1.50:8443",
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
        if self.path not in {"/api/engine/start", "/api/engine/stop", "/api/engine/restart"}:
            self.send_error(404)
            return
        if self.path.endswith("stop"):
            STATE.update(state="stopped", phase="Stopped", memoryBytes=0)
        else:
            STATE.update(state="starting", phase="Loading model (visual QA fixture)")

            def ready():
                STATE.update(state="ready", phase="Ready", memoryBytes=4_500_000_000)

            threading.Timer(2, ready).start()
        self.json(STATE)

    def do_PUT(self):
        if self.path != "/api/settings":
            self.send_error(404)
            return
        SETTINGS.update(
            json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 32768)))
        )
        self.json(SETTINGS)


if __name__ == "__main__":
    print(
        "Visual QA fixture: http://127.0.0.1:18765 — synthetic state, no engine or audio",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", 18765), Preview).serve_forever()
