"""Own preview fixture for main-webapp settings QA; no real controller or model."""

import os
import subprocess
import sys
import threading
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
    try:
        result = subprocess.run(
            [
                "node",
                "tests/browser/providers.mjs",
                sys.argv[1],
                f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=ROOT,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": str(ROOT / ".tmp") + "/",
            },
            check=False,
        )
        return result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    sys.exit(main())
