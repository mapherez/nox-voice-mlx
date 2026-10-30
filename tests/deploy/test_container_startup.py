"""CI checks the actual Linux image with private, differently owned secret mounts."""

import json
import os
import shutil
import stat
import subprocess
import time

import pytest
from controller.certificates import create_certificates

IMAGE = os.environ.get("NOX_VOICE_CONTAINER_TEST_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE or not shutil.which("docker"), reason="Requires the CI-built Docker image"
)


def docker(*arguments):
    return subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, check=True, timeout=30
    ).stdout.strip()


def test_private_host_secrets_work_without_chown_or_chmod(tmp_path):
    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    google = tmp_path / "google-client-secret"
    google.write_text("test-only-google-secret")
    session = tmp_path / "session-secret"
    session.write_text("test-only-session-secret-at-least-thirty-two-characters")
    inputs = {
        "google_client_secret": google,
        "session_secret": session,
        "controller_ca": bundle / "ca.crt",
        "controller_client_cert": bundle / "client.crt",
        "controller_client_key": bundle / "client.key",
    }
    variables = {
        "NOX_VOICE_GOOGLE_CLIENT_SECRET_FILE": "google_client_secret",
        "NOX_VOICE_SESSION_SECRET_FILE": "session_secret",
        "NOX_VOICE_CONTROLLER_CA_FILE": "controller_ca",
        "NOX_VOICE_CONTROLLER_CERT_FILE": "controller_client_cert",
        "NOX_VOICE_CONTROLLER_KEY_FILE": "controller_client_key",
    }
    snapshots = {}
    for path in inputs.values():
        path.chmod(0o600)
        assert path.stat().st_uid != 10001, "Test must exercise a different host owner"
        snapshots[path] = (path.stat(), path.read_bytes())

    arguments = [
        "run",
        "--detach",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--tmpfs=/tmp:size=32m,mode=1777",
        "--tmpfs=/data:uid=10001,gid=10001,mode=700",
        "--env=NOX_VOICE_OWNER_EMAIL=owner@example.test",
        "--env=NOX_VOICE_GOOGLE_CLIENT_ID=test-client",
        "--env=NOX_VOICE_CONTROLLER_URL=https://127.0.0.1:8443",
    ]
    for capability in ("CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID"):
        arguments += ["--cap-add", capability]
    for variable, name in variables.items():
        arguments += ["--env", f"{variable}=/run/secrets/{name}"]
    for name, path in inputs.items():
        arguments += ["--mount", f"type=bind,source={path},target=/run/secrets/{name},readonly"]

    container = docker(*arguments, IMAGE)
    try:
        deadline = time.monotonic() + 20
        while True:
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=1)",
                ],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode == 0:
                break
            assert time.monotonic() < deadline, docker("logs", container)
            assert docker("inspect", "--format={{.State.Running}}", container) == "true", docker(
                "logs", container
            )
            time.sleep(0.2)

        status = docker("exec", container, "python", "-c", "print(open('/proc/1/status').read())")
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        assert fields["Uid"].split() == ["10001"] * 4
        assert fields["Gid"].split() == ["10001"] * 4
        assert not fields["Groups"].strip()
        assert int(fields["CapEff"].strip(), 16) == 0
        assert fields["NoNewPrivs"].strip() == "1"

        # Check the actual gateway-served React shell and its production assets.
        assets = json.loads(docker("exec", container, "python", "-c", """
import json, re, urllib.request
origin = 'http://127.0.0.1:8080'
page = urllib.request.urlopen(origin + '/').read().decode()
assert 'id="root"' in page
paths = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
assert paths
results = []
for path in paths:
    response = urllib.request.urlopen(origin + path)
    assert response.status == 200 and response.read()
    results.append([path, response.headers.get_content_type()])
print(json.dumps(results))
"""))
        assert any(path.endswith(".js") and kind in {"text/javascript", "application/javascript"}
            for path, kind in assets)
        assert any(path.endswith(".css") and kind == "text/css" for path, kind in assets)

        for path, (before, contents) in snapshots.items():
            after = path.stat()
            assert path.read_bytes() == contents
            assert stat.S_IMODE(after.st_mode) == 0o600
            assert (after.st_uid, after.st_gid, after.st_mtime_ns) == (
                before.st_uid,
                before.st_gid,
                before.st_mtime_ns,
            )
    finally:
        docker("rm", "--force", container)
