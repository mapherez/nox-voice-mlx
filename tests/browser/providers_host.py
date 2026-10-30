"""Own preview fixture for main-webapp settings QA; no real controller or model."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    spec = spec_from_file_location("providers_preview", ROOT / "tests" / "preview.py")
    preview = module_from_spec(spec)
    spec.loader.exec_module(preview)
    server = ThreadingHTTPServer(("127.0.0.1", 0), preview.Preview)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dev = None
    dev_log = None
    try:
        frontend_url = None
        if "--dev" in sys.argv[2:]:
            (ROOT / ".tmp").mkdir(exist_ok=True)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            dev_log = (ROOT / ".tmp" / "react-dev-server.log").open("w")
            dev = subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", str(port)],
                cwd=ROOT / "frontend", stdout=dev_log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            frontend_url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                assert dev.poll() is None, "Dev server exited; see .tmp/react-dev-server.log"
                try:
                    with urllib.request.urlopen(frontend_url, timeout=1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "Dev server did not start"
                    time.sleep(0.05)
        result = subprocess.run(
            [
                "node",
                "tests/browser/providers.mjs",
                sys.argv[1],
                f"http://127.0.0.1:{server.server_port}",
            ] + ([frontend_url] if frontend_url else []),
            cwd=ROOT,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": str(ROOT / ".tmp") + "/",
            },
            check=False,
        )
        return result.returncode
    finally:
        if dev is not None:
            try:
                os.killpg(dev.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            dev.wait(timeout=10)
        if dev_log is not None:
            dev_log.close()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    sys.exit(main())
