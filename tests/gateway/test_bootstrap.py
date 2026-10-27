import os
import stat
from dataclasses import replace

import pytest
from controller.certificates import create_certificates
from nox_voice_gateway import bootstrap
from nox_voice_gateway.config import Config


def test_staging_keeps_private_host_files_unchanged(tmp_path):
    source = tmp_path / "host-secrets"
    source.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    environment = {"UNRELATED_FILE": "leave-this-alone"}
    snapshots = {}
    for variable in bootstrap.SECRET_VARIABLES:
        path = source / variable.lower()
        path.write_bytes(f"private test value: {variable}".encode())
        path.chmod(0o600)
        environment[variable] = str(path)
        snapshots[path] = (path.stat(), path.read_bytes())

    bootstrap.stage_secrets(environment, runtime, os.getuid(), os.getgid())

    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert environment["UNRELATED_FILE"] == "leave-this-alone"
    for variable in bootstrap.SECRET_VARIABLES:
        destination = runtime / variable.lower()
        assert environment[variable] == str(destination)
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        assert destination.stat().st_uid == os.getuid()
        assert destination.stat().st_gid == os.getgid()
        original = source / variable.lower()
        previous, contents = snapshots[original]
        assert destination.read_bytes() == contents
        assert original.read_bytes() == contents
        current = original.stat()
        assert (current.st_uid, current.st_gid, current.st_mode, current.st_mtime_ns) == (
            previous.st_uid,
            previous.st_gid,
            previous.st_mode,
            previous.st_mtime_ns,
        )


@pytest.mark.parametrize("invalid_source", ["missing", "directory"])
def test_staging_failure_removes_partial_copies_and_keeps_environment(tmp_path, invalid_source):
    source = tmp_path / invalid_source
    if invalid_source == "directory":
        source.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    variable = bootstrap.SECRET_VARIABLES[0]
    environment = {variable: str(source)}
    with pytest.raises(RuntimeError, match=f"Cannot prepare {variable}"):
        bootstrap.stage_secrets(environment, runtime, os.getuid(), os.getgid())
    assert not runtime.exists()
    assert environment == {variable: str(source)}


def test_privilege_drop_removes_supplementary_groups_before_uid(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap.os, "setgroups", lambda groups: calls.append(("groups", groups)))
    monkeypatch.setattr(bootstrap.os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(bootstrap.os, "setuid", lambda uid: calls.append(("uid", uid)))
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: bootstrap.APP_UID)
    monkeypatch.setattr(bootstrap.os, "getegid", lambda: bootstrap.APP_GID)
    bootstrap.drop_privileges()
    assert calls == [("groups", []), ("gid", 10001), ("uid", 10001)]


def test_bootstrap_checks_tls_after_dropping_privileges_before_exec(config, tmp_path, monkeypatch):
    bundle = create_certificates(tmp_path / "tls", "127.0.0.1")
    config = replace(
        config,
        ca_file=bundle / "ca.crt",
        cert_file=bundle / "client.crt",
        key_file=bundle / "client.key",
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    calls = []
    monkeypatch.setattr(bootstrap, "APP_UID", os.getuid())
    monkeypatch.setattr(bootstrap, "APP_GID", os.getgid())
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(bootstrap.os, "environ", {})
    monkeypatch.setattr(bootstrap.os, "umask", lambda mask: None)
    monkeypatch.setattr(bootstrap.tempfile, "mkdtemp", lambda **kwargs: str(runtime))
    monkeypatch.setattr(bootstrap, "drop_privileges", lambda: calls.append("drop"))

    def configuration(cls):
        calls.append("validate")
        return config

    monkeypatch.setattr(Config, "from_env", classmethod(configuration))
    monkeypatch.setattr(bootstrap.os, "execvp", lambda *args: calls.append(("exec", args)))
    bootstrap.main()
    assert calls == ["drop", "validate", ("exec", ("nox-voice-web", ["nox-voice-web"]))]


def test_bad_tls_stops_startup_instead_of_serving_broken_api(config, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(bootstrap.os, "umask", lambda mask: None)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(bootstrap.os, "execvp", lambda *args: pytest.fail("Must not start server"))
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 1
    assert "Controller CA certificate is missing" in capsys.readouterr().err
