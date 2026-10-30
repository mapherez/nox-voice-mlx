import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from controller import credentials, hub
from controller.config import ControllerConfig
from controller.credentials import Credentials
from controller.model_library import ModelLibrary
from controller.server import create_app
from controller.supervisor import Supervisor
from fastapi import HTTPException
from fastapi.testclient import TestClient


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


@pytest.mark.parametrize("range_supported", [False, True])
def test_partial_download_resumes_or_restarts_without_duplicating_bytes(tmp_path, monkeypatch, range_supported):
    import hashlib

    body = b"a verified complete model"
    expected = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    target = tmp_path / "model.gguf"
    target.with_suffix(".gguf.part").write_bytes(body[:8])
    target.with_suffix(".gguf.part.json").write_text(json.dumps({**expected, "blobId": None}))
    calls, progress = [], []

    def request(url, token, headers):
        calls.append(headers)
        return Response(body[8:], 206, {"Content-Range": f"bytes 8-{len(body)-1}/{len(body)}"}) if range_supported else Response(body)

    monkeypatch.setattr(hub, "request", request)
    hub.download("https://source.invalid/model", target, expected, lambda *value: progress.append(value))
    assert calls == [{"Range": "bytes=8-"}]
    assert target.read_bytes() == body
    assert not target.with_suffix(".gguf.part").exists()
    assert progress[-1] == (len(body), 0, "downloaded")


def test_invalid_range_cannot_append_unrelated_bytes(tmp_path, monkeypatch):
    target = tmp_path / "model"
    (tmp_path / "model.part").write_bytes(b"saved")
    (tmp_path / "model.part.json").write_text(json.dumps({"size": 10, "sha256": "0"*64, "blobId": None}))
    monkeypatch.setattr(hub, "request", lambda *_: Response(b"wrong", 206, {"Content-Range": "bytes 0-4/10"}))
    with pytest.raises(hub.DownloadError, match="invalid partial"):
        hub.download("https://example.invalid/model", target, {"size": 10, "sha256": "0"*64}, lambda *_: None)
    assert not target.exists() and (tmp_path / "model.part").read_bytes() == b"saved"


def test_bad_checksum_is_never_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "request", lambda *_: Response(b"bad"))
    with pytest.raises(hub.DownloadError, match="integrity"):
        hub.download("https://example.invalid/model", tmp_path / "model", {"size": 3, "sha256": "0"*64}, lambda *_: None)
    assert not (tmp_path / "model").exists() and not (tmp_path / "model.part").exists()


def test_a_different_artifact_identity_restarts_instead_of_appending(tmp_path, monkeypatch):
    import hashlib

    (tmp_path / "model.part").write_bytes(b"unrelated artifact")
    (tmp_path / "model.part.json").write_text(json.dumps({"size": 18, "sha256": "0"*64}))
    headers = []

    def request(url, token, values):
        headers.append(values)
        return Response(b"new model")

    monkeypatch.setattr(hub, "request", request)
    hub.download("https://example.invalid/model", tmp_path / "model", {"size": 9, "sha256": hashlib.sha256(b"new model").hexdigest()}, lambda *_: None)
    assert headers == [{}] and (tmp_path / "model").read_bytes() == b"new model"


def test_redirect_drops_huggingface_token_for_other_hosts():
    from urllib.request import Request

    source = Request("https://huggingface.co/model", headers={"Authorization": "Bearer secret"})
    result = hub.SafeRedirect().redirect_request(source, None, 302, "Found", {}, "https://cdn.example/file?signature=opaque")
    assert not result.has_header("Authorization")
    with pytest.raises(hub.DownloadError, match="insecure"):
        hub.SafeRedirect().redirect_request(source, None, 302, "Found", {}, "http://cdn.example/file")


def test_credentials_private_status_only_replace_remove_and_validation_redaction(tmp_path):
    config = ControllerConfig(data_dir=tmp_path)
    with TestClient(create_app(config)) as client:
        path = "/api/voice/v1/credentials/huggingface"
        secret = "hf_privateTestToken123"
        result = client.put(path, json={"secret": secret})
        assert result.status_code == 200 and secret not in result.text
        assert secret not in client.get("/api/voice/v1/credentials").text
        assert (tmp_path / "credentials.json").stat().st_mode & 0o077 == 0
        invalid = client.put(path, json={"secret": secret, "extra": secret})
        assert invalid.status_code == 422 and secret not in invalid.text
        client.put(path, json={"secret": "hf_replacementToken123"})
        assert secret not in (tmp_path / "credentials.json").read_text()
        assert client.delete(path).status_code == 200
        assert "replacement" not in (tmp_path / "credentials.json").read_text()


@pytest.mark.parametrize("failure, state", [("invalid", "invalid"), ("access", "access_required"), ("network", "network_error"), (None, "valid")])
def test_credential_test_distinguishes_identity_model_access_and_network(tmp_path, monkeypatch, failure, state):
    store = Credentials(ControllerConfig(data_dir=tmp_path))
    store.save("huggingface", "hf_testToken12345")

    def identity(*_):
        if failure == "invalid":
            raise hub.DownloadError("MODEL_ACCESS", "denied")
        if failure == "network":
            raise hub.DownloadError("NETWORK", "network unavailable")
        return {"name": "fixture"}

    def access(*_, **__):
        if failure == "access":
            raise hub.DownloadError("MODEL_ACCESS", "Accept model access terms.")
        return Response(b"")

    monkeypatch.setattr(credentials, "hub_json", identity)
    monkeypatch.setattr(credentials, "request", access)
    assert store.test("huggingface")["state"] == state
    assert store.describe()["services"][0]["test"]["state"] == state


def test_slow_credential_test_cannot_certify_a_replacement(tmp_path, monkeypatch):
    store = Credentials(ControllerConfig(data_dir=tmp_path))
    store.save("huggingface", "hf_originalToken123")

    def test_old_token(token):
        store.save("huggingface", "hf_replacementToken123")
        return {"state": "valid", "message": "old token valid"}

    monkeypatch.setitem(credentials.TESTERS, "huggingface", test_old_token)
    assert store.test("huggingface")["state"] == "changed"
    assert store.describe()["services"][0]["test"] is None


def test_credential_registry_supports_other_services_without_huggingface_format(tmp_path, monkeypatch):
    monkeypatch.setitem(credentials.SERVICES, "fixture-service", {"id": "fixture-service", "name": "Fixture service", "fieldLabel": "API key", "help": "Fixture"})
    store = Credentials(ControllerConfig(data_dir=tmp_path))
    result = store.save("fixture-service", "api_fixtureSecret123")
    entry = next(item for item in result["services"] if item["id"] == "fixture-service")
    assert entry["configured"] and not entry["testSupported"]
    assert "api_fixtureSecret" not in json.dumps(result)
    assert store.test("fixture-service")["state"] == "unsupported"
    store.remove("fixture-service")
    assert store.secret("fixture-service") is None


def test_personaplex_worker_prepares_complete_verified_installation_without_exposing_token(tmp_path, monkeypatch):
    import hashlib
    import tarfile

    from controller import download_worker, model_files
    from controller.config import VOICE_NAMES

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for voice in VOICE_NAMES:
            content = b"fixture voice"
            member = tarfile.TarInfo(f"voices/{voice}.pt")
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    contents = {name: b"fixture model" for name in model_files.ASSETS}
    contents["voices.tgz"] = archive.getvalue()
    files = [{"name": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()} for name, body in contents.items()]
    monkeypatch.setattr(model_files, "artifact_manifest", lambda: {"revision": "a"*40, "files": files})
    monkeypatch.setattr(hub, "hub_json", lambda *_: {"sha": "a"*40, "siblings": [
        {"rfilename": item["name"], "lfs": {"size": item["size"], "sha256": item["sha256"]}} for item in files]})
    monkeypatch.setattr(hub, "request", lambda url, *_: Response(contents[url.rsplit("/", 1)[-1]]))
    events = []
    monkeypatch.setattr(download_worker, "emit", lambda **value: events.append(json.loads(json.dumps(value))))
    root = tmp_path / "personaplex"
    download_worker.run("personaplex", root, "hf_neverExposeThis123")
    assert model_files.verify_model(root)["revision"] == "a"*40
    assert len(list((root / "voices_cpu").glob("*.pt"))) == 18
    assert events[-1] == {"phase": "complete"}
    assert "hf_neverExpose" not in json.dumps(events)
    assert not list(root.glob("*.part"))


async def test_download_owned_process_token_stdin_and_recovery_after_restart(tmp_path, monkeypatch):
    manager = Supervisor(ControllerConfig(data_dir=tmp_path, weights_dir=tmp_path / "weights"))
    manager.library.credentials.save("huggingface", "hf_notInProcessArguments123")
    calls, received = [], bytearray()
    waiting = asyncio.Event()

    class Process:
        returncode = None
        stdout = asyncio.StreamReader()
        stdin = SimpleNamespace(write=received.extend, drain=lambda: asyncio.sleep(0), close=lambda: None)

        async def wait(self):
            await waiting.wait()
            return self.returncode

        def terminate(self):
            self.returncode = -15
            self.stdout.feed_eof()
            waiting.set()

        kill = terminate

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        job = await manager.library.download("personaplex")
        assert await manager.library.download("personaplex") is job
        for _ in range(5):
            await asyncio.sleep(0)
        assert "hf_notIn" not in str(calls)
        assert json.loads(received)["token"] == "hf_notInProcessArguments123"
        assert "hf_notIn" not in (tmp_path / "downloads.json").read_text()
        with pytest.raises(HTTPException, match="409"):
            await manager.start()
        await manager.library.close()
        assert job["state"] == "interrupted"
        restored = ModelLibrary(manager)
        assert list(restored.jobs.values())[0]["state"] == "interrupted"
        assert not restored.tasks  # Explicit retry is required after restart.
    finally:
        await manager.close()


async def test_folder_action_opens_only_provider_directory_in_native_finder(tmp_path, monkeypatch):
    from controller import model_library

    manager = Supervisor(ControllerConfig(data_dir=tmp_path, weights_dir=tmp_path / "weights"))
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(wait=lambda: asyncio.sleep(0, result=0))

    original_stat = model_library.os.stat
    monkeypatch.setattr(model_library.sys, "platform", "darwin")
    monkeypatch.setattr(model_library.os, "getuid", lambda: 501)
    monkeypatch.setattr(model_library.os, "stat", lambda path, *args, **kwargs: SimpleNamespace(st_uid=501) if str(path) == "/dev/console" else original_stat(path, *args, **kwargs))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        assert "Finder" in (await manager.library.open_folder("lfm3-audio"))["message"]
        assert calls == [("/usr/bin/open", "-a", "Finder", str(tmp_path / "weights/lfm3-audio"))]
    finally:
        await manager.close()
