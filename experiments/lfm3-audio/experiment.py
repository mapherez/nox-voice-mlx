#!/usr/bin/env python3
"""Self-contained, manually started voice experiment. Python 3.9+; stdlib only."""

import argparse
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import platform
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from delegation import DelegationManager
from mcp_client import MCPError, MCPManager

ROOT = Path(__file__).resolve().parent
ASSETS = {"/": "index.html", "/app.js": "app.js", "/audio.js": "audio.js", "/tools.js": "tools.js",
          "/capture.js": "capture.js", "/style.css": "style.css"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8"}


def local_path(relative):
    path = ROOT / relative
    if os.path.commonpath([str(ROOT), str(path.resolve())]) != str(ROOT):
        raise ValueError("Experiment paths must stay inside this directory: " + str(relative))
    return path


def manifest():
    return json.loads((ROOT / "artifacts.json").read_text())


@contextlib.contextmanager
def exclusive_operation():
    temporary = local_path(".tmp")
    temporary.mkdir(exist_ok=True)
    with local_path(".tmp/experiment.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This experiment is already preparing or running. Stop it first.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def checked(path, artifact):
    return path.is_file() and path.stat().st_size == artifact["size"] and digest(path) == artifact["sha256"]


def download(url, destination, artifact):
    """Download into our own directory, verify, then atomically publish the file."""
    if checked(destination, artifact):
        print("Already verified: " + destination.name, flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = local_path(".tmp")
    temporary.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".part", dir=temporary)
    part = Path(name)
    try:
        opener = build_opener(ProxyHandler({}))
        request = Request(url, headers={"User-Agent": "NoX-Voice-LFM3-Experiment"})
        size, last_report = 0, 0.0
        sha = hashlib.sha256()
        with os.fdopen(fd, "wb") as output, opener.open(request, timeout=60) as response:
            if response.status != 200:
                raise RuntimeError("Download failed: HTTP " + str(response.status))
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > artifact["size"]:
                    raise RuntimeError("Download exceeds its pinned size.")
                sha.update(block)
                output.write(block)
                if time.monotonic() - last_report > 3:
                    print("{}: {:.0f}% ({:.0f} MB)".format(
                        destination.name, size * 100 / artifact["size"], size / 1_000_000), flush=True)
                    last_report = time.monotonic()
        if size != artifact["size"] or sha.hexdigest() != artifact["sha256"]:
            raise RuntimeError("Download checksum or size does not match: " + destination.name)
        part.replace(destination)
        print("Verified: " + destination.name, flush=True)
    finally:
        part.unlink(missing_ok=True)


def extract_runtime(archive, destination):
    """Handle only files, directories and links whose targets remain inside the bundle."""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            target = destination / member.name
            if (Path(member.name).is_absolute()
                    or ".." in Path(member.name).parts
                    or os.path.commonpath([str(destination), str(target.resolve())]) != str(destination)):
                raise ValueError("Unsafe archive path.")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("Unsupported archive entry.")
            if any(parent in {entry.name.rstrip("/") for entry in members if entry.issym()}
                   for parent in [str(path) for path in Path(member.name).parents]):
                raise ValueError("Archive entry is nested below a link.")
            if member.issym():
                link = target.parent / member.linkname
                if (Path(member.linkname).is_absolute()
                        or os.path.commonpath([str(destination), str(link.resolve())]) != str(destination)):
                    raise ValueError("Unsafe archive link.")
        # Create links last; no archive entry may write through a link.
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content, target.open("wb") as output:
                    shutil.copyfileobj(content, output)
                target.chmod(member.mode & 0o755)
        for member in members:
            if member.issym():
                target = destination / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)


def runtime_binary():
    runtime = local_path("runtime")
    candidates = list(runtime.rglob("llama-server")) if runtime.exists() else []
    if len(candidates) != 1:
        raise RuntimeError("Runtime is missing. Run: python3 experiment.py prepare")
    binary = candidates[0]
    local_path(binary.relative_to(ROOT))
    return binary


def prepare(runtime_only=False):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This pinned runtime requires macOS on Apple Silicon.")
    data = manifest()
    runtime = local_path("runtime")
    cache = local_path(".cache")
    cache.mkdir(exist_ok=True)
    archive = cache / "llama-runtime.tar.gz"
    download(data["runtime"]["url"], archive, data["runtime"])
    tmp = local_path(".tmp")
    tmp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=tmp) as directory:
        staging = Path(directory)
        extract_runtime(archive, staging)
        if not list(staging.rglob("llama-server")):
            raise RuntimeError("The runtime archive has no llama-server.")
        if runtime.exists():
            shutil.rmtree(runtime)
        shutil.copytree(staging, runtime, symlinks=True)
    archive.unlink()
    if not runtime_only:
        model = data["model"]
        for artifact in model["files"]:
            url = "https://huggingface.co/{}/resolve/{}/{}".format(
                model["repository"], model["revision"], artifact["name"])
            download(url, local_path("models/" + artifact["name"]), artifact)
    print("Preparation complete. All downloaded files are inside: " + str(ROOT), flush=True)


def runtime_environment():
    cache, temporary = local_path(".cache"), local_path(".tmp")
    cache.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    # No user tokens, model paths, proxy settings or inherited llama flags.
    environment = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
                   "TMPDIR": str(temporary) + "/", "XDG_CACHE_HOME": str(cache),
                   "HF_HOME": str(cache / "huggingface"),
                   "HF_HUB_CACHE": str(cache / "huggingface" / "hub"),
                   "LLAMA_CACHE": str(cache / "llama"), "HF_HUB_OFFLINE": "1"}
    return environment


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    # Restart after Ctrl+C even when closed HTTP/WebSocket connections are in TIME_WAIT.
    # This is SO_REUSEADDR, not SO_REUSEPORT: an active listener still blocks the port.
    allow_reuse_address = True

    def __init__(self, address, backend_port, api_key):
        super().__init__(address, Handler)
        self.backend_port = backend_port
        self.api_key = api_key
        self.connections = set()
        self.connections_lock = threading.Lock()
        self.mcp = MCPManager()
        self.delegation = DelegationManager(self.mcp)

    def server_close(self):
        super().server_close()
        if hasattr(self, "delegation"):
            self.delegation.clear()
        if hasattr(self, "mcp"):
            self.mcp.close()

    def close_connections(self):
        with self.connections_lock:
            for connection in self.connections:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
            self.connections.clear()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # Do not persist requests, prompts or conversation audio.

    def same_origin(self, require_origin=False):
        port = self.server.server_port
        hosts = {"localhost:" + str(port), "127.0.0.1:" + str(port)}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return (host in hosts and (origin == "http://" + host
                or (origin is None and not require_origin)))

    def reply(self, status, body, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                         "script-src 'self'; style-src 'self'; worker-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.same_origin():
            self.reply(403, b'{"error":"Local origin required."}')
            return
        if self.path == "/realtime":
            if not self.same_origin(require_origin=True):
                self.reply(403, b'{"error":"Same origin required."}')
                return
            self.realtime()
        elif self.path == "/api/status":
            self.reply(200, b'{"ready":true,"model":"LFM3-Audio-2B Q8_0"}')
        elif self.path == "/api/mcp":
            self.reply(200, json.dumps(self.server.mcp.profile()).encode())
        elif self.path == "/api/delegation":
            self.reply(200, json.dumps(self.server.delegation.profile()).encode())
        elif self.path in ASSETS:
            path = ROOT / "app" / ASSETS[self.path]
            self.reply(200, path.read_bytes(), MIME[path.suffix])
        else:
            self.reply(404, b'{"error":"Not found."}')

    def do_POST(self):
        if not self.same_origin(require_origin=True):
            self.reply(403, b'{"error":"Same origin required."}')
            self.close_connection = True
            return
        if self.path not in {"/api/mcp/connect", "/api/mcp/disconnect", "/api/mcp/call",
                             "/api/delegation/configure", "/api/delegation/generate",
                             "/api/delegation/execute", "/api/delegation/discard"}:
            self.reply(404, b'{"error":"Not found."}')
            self.close_connection = True
            return
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("Send application/json.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request size (maximum 1 MB).")
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Invalid JSON number.")))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/mcp/connect":
                result = self.server.mcp.connect(body.get("url"))
                self.server.delegation.clear()
            elif self.path == "/api/mcp/disconnect":
                self.server.mcp.close()
                self.server.delegation.clear()
                result = self.server.mcp.profile()
            elif self.path == "/api/mcp/call":
                result = self.server.mcp.execute(body.get("connectionId"), body.get("invocationId"),
                                                 body.get("name"), body.get("arguments"))
            elif self.path == "/api/delegation/configure":
                result = self.server.delegation.configure(body.get("url"), body.get("model"),
                                                          body.get("connectionId"), body.get("target"))
            elif self.path == "/api/delegation/generate":
                result = self.server.delegation.generate(body.get("configurationId"), body.get("connectionId"),
                                                         body.get("invocationId"), body.get("task"))
            elif self.path == "/api/delegation/execute":
                result = self.server.delegation.execute(body.get("draftId"))
            else:
                result = self.server.delegation.discard(body.get("draftId"))
            self.reply(200, json.dumps(result, allow_nan=False).encode())
        except (MCPError, ValueError, OSError) as error:
            self.close_connection = True
            self.reply(400, json.dumps({"error": str(error)}).encode())

    def realtime(self):
        if (self.headers.get("Upgrade", "").lower() != "websocket"
                or self.headers.get("Sec-WebSocket-Version") != "13"):
            self.reply(400, b'{"error":"WebSocket required."}')
            return
        self.close_connection = True
        try:
            with socket.create_connection(("127.0.0.1", self.server.backend_port), timeout=10) as backend:
                headers = ["GET /v1/realtime HTTP/1.1",
                           "Host: 127.0.0.1:" + str(self.server.backend_port),
                           "Upgrade: websocket", "Connection: Upgrade",
                           "Sec-WebSocket-Version: 13",
                           "Sec-WebSocket-Key: " + self.headers.get("Sec-WebSocket-Key", ""),
                           "Authorization: Bearer " + self.server.api_key, "", ""]
                backend.sendall("\r\n".join(headers).encode("ascii"))
                with self.server.connections_lock:
                    self.server.connections.update([self.connection, backend])
                try:
                    # Relay the upgrade and frames; browser Origin is checked above.
                    backend.settimeout(30)
                    self.connection.settimeout(30)
                    while True:
                        readable, _, _ = select.select([self.connection, backend], [], [], 1)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = backend if source is self.connection else self.connection
                            target.sendall(data)
                finally:
                    with self.server.connections_lock:
                        self.server.connections.discard(self.connection)
                        self.server.connections.discard(backend)
        except OSError:
            return


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(process, port, key, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("llama-server exited. See its messages above.")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/health", headers={"Authorization": "Bearer " + key})
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.25)
    raise RuntimeError("Model loading timed out. Stop and inspect the runtime output.")


def serve(port):
    data = manifest()
    binary = runtime_binary()
    paths = [local_path("models/" + item["name"]) for item in data["model"]["files"]]
    print("Verifying model files…", flush=True)
    for path, artifact in zip(paths, data["model"]["files"]):
        if not checked(path, artifact):
            raise RuntimeError("Model is missing or invalid. Run: python3 experiment.py prepare")
    backend_port, key = free_port(), secrets.token_urlsafe(32)
    # Reserve the public port before starting a model process.
    server = LocalServer(("127.0.0.1", port), backend_port, key)
    environment = runtime_environment()
    environment["LLAMA_API_KEY"] = key
    command = [str(binary), "--model", str(paths[0]), "--mmproj", str(paths[1]),
               "--host", "127.0.0.1", "--port", str(backend_port), "--offline",
               "--ctx-size", "32768", "--parallel", "1", "--n-gpu-layers", "99",
               "--jinja", "--no-warmup", "--no-webui-mcp-proxy", "--no-slots",
               "--log-verbosity", "2"]
    process = None
    previous = {}

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, interrupted)
        print("Loading LFM3-Audio-2B. Nothing starts automatically at login.", flush=True)
        process = subprocess.Popen(command, cwd=ROOT, env=environment, start_new_session=True)
        wait_ready(process, backend_port, key)
        print("\nOpen http://localhost:{}\nHold Space to speak. Release to send.\n"
              "Press Ctrl+C here to stop the experiment and unload the model.\n".format(port), flush=True)
        server.timeout = 0.5
        while process.poll() is None:
            server.handle_request()
        raise RuntimeError("The model process stopped. The experiment has closed.")
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        server.close_connections()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        server.server_close()
        print("Experiment stopped. No model process is left running.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("prepare", help="Download and verify the local runtime and model.")
    setup.add_argument("--runtime-only", action="store_true", help=argparse.SUPPRESS)
    run = sub.add_parser("run", help="Start manually on localhost; Ctrl+C stops everything.")
    run.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with exclusive_operation():
        if args.command == "prepare":
            prepare(args.runtime_only)
        else:
            if not 1024 <= args.port <= 65535:
                parser.error("port must be between 1024 and 65535")
            serve(args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as error:
        print("Error: " + str(error), file=sys.stderr)
        sys.exit(1)
