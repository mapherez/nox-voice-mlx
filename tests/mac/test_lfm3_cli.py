import sys

import pytest
from controller import cli
from controller.config import ControllerConfig, save_config
from engine.lfm3 import assets


@pytest.mark.parametrize("override", [False, True])
def test_preparation_uses_configured_weights_unless_explicitly_overridden(
    tmp_path, monkeypatch, override
):
    path = tmp_path / "controller.json"
    config = ControllerConfig(data_dir=tmp_path, weights_dir=tmp_path / "configured-weights")
    save_config(path, config)
    source = tmp_path / "explicit-experiment"
    calls = []
    monkeypatch.setattr(assets, "prepare", lambda *args: calls.append(args))
    arguments = [
        "nox-voice",
        "--config",
        str(path),
        "prepare-model",
        "--provider",
        "lfm3-audio",
        "--from-experiment",
        str(source),
    ]
    target = tmp_path / "override" if override else config.weights_dir
    if override:
        arguments += ["--weights-dir", str(target)]
    monkeypatch.setattr(sys, "argv", arguments)
    cli.main()
    assert calls == [(assets.asset_root(target), source)]
    assert not target.exists()  # Stub preparation never touches model files.


def test_offline_check_is_read_only_and_does_not_prepare(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "MAC_ROOT", tmp_path / "installation")
    monkeypatch.setattr(assets, "verify", lambda root: calls.append(root))
    monkeypatch.setattr(assets, "prepare", lambda *_: pytest.fail("Check must never prepare"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nox-voice",
            "--config",
            str(tmp_path / "not-configured.json"),
            "prepare-model",
            "--provider",
            "lfm3-audio",
            "--check",
        ],
    )
    cli.main()
    assert calls == [tmp_path / "installation/weights/lfm3-audio"]
    assert not (tmp_path / "installation").exists()
