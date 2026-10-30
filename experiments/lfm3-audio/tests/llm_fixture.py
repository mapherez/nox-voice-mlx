"""Disposable LM Studio native-API fixture; no real model or user data."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTENT = "# Test note\n\n## Checklist\n- First item\n- Second item\n\nUnicode stays intact: ação, café.\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        fixture = self.server.fixture
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        fixture.requests.append(body)
        fixture.started.set()
        fixture.release.wait(timeout=5)
        output = fixture.output if fixture.output is not None else [
            {"type": "reasoning", "content": "This must never become note content."},
            {"type": "message", "content": json.dumps({"title": "Delegated note", "content": CONTENT})},
        ]
        data = json.dumps({"output": output}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LLMFixture:
    def __init__(self, output=None, blocked=False):
        self.output = output
        self.requests = []
        self.started, self.release = threading.Event(), threading.Event()
        if not blocked:
            self.release.set()

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.fixture = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}/api/v1/chat".format(self.server.server_port)
        return self

    def __exit__(self, *_args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
