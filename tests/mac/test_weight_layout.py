import json
import sys

import pytest
from controller import cli, model_files
from controller.config import VOICE_NAMES, ControllerConfig, save_config
from controller.providers import provider
from controller.weight_layout import migrate_weights
from engine.lfm3 import assets


def test_migration_preserves_manifest_contents_inodes_and_other_files(tmp_path):
    weights = tmp_path / "weights"
    names = [*model_files.ASSETS, *[f"voices_cpu/{voice}.pt" for voice in VOICE_NAMES]]
    hashes = {}
    for name in names:
        path = weights / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        hashes[name] = model_files.digest(path)
    manifest = {"repository": model_files.REPOSITORY, "revision": "a" * 40, "files": hashes}
    (weights / "manifest.json").write_text(json.dumps(manifest))
    (weights / ".cache").mkdir()
    (weights / ".cache/hub-marker").write_bytes(b"cache")
    (weights / "unrelated.txt").write_bytes(b"keep")
    legacy_lfm3 = weights / "providers/lfm3-audio"
    (legacy_lfm3 / "runtime").mkdir(parents=True)
    (legacy_lfm3 / "runtime/llama-server").write_bytes(b"runtime")
    (legacy_lfm3 / "installed.json").write_bytes(b"unchanged inventory")
    inode = (weights / "model.safetensors").stat().st_ino

    assert migrate_weights(weights) > 0
    assert model_files.verify_model(weights / "personaplex") == manifest
    assert (weights / "personaplex/model.safetensors").stat().st_ino == inode
    assert (weights / "personaplex/.cache/hub-marker").read_bytes() == b"cache"
    assert (weights / "lfm3-audio/installed.json").read_bytes() == b"unchanged inventory"
    assert (weights / "lfm3-audio/runtime/llama-server").read_bytes() == b"runtime"
    assert (weights / "unrelated.txt").read_bytes() == b"keep"
    assert not (weights / "providers").exists()
    assert migrate_weights(weights) == 0


def test_migration_checks_all_conflicts_before_moving_any_assets(tmp_path):
    (tmp_path / "personaplex").mkdir()
    (tmp_path / "model.safetensors").write_bytes(b"model")
    (tmp_path / "voices.tgz").write_bytes(b"legacy voices")
    (tmp_path / "personaplex/voices.tgz").write_bytes(b"different voices")
    with pytest.raises(ValueError, match="conflict"):
        migrate_weights(tmp_path)
    assert (tmp_path / "model.safetensors").read_bytes() == b"model"
    assert not (tmp_path / "personaplex/model.safetensors").exists()
    assert (tmp_path / "voices.tgz").read_bytes() == b"legacy voices"
    assert (tmp_path / "personaplex/voices.tgz").read_bytes() == b"different voices"


def test_identical_bundled_files_are_consolidated_on_upgrade(tmp_path):
    (tmp_path / "personaplex").mkdir()
    for path in [tmp_path / "config.json", tmp_path / "personaplex/config.json"]:
        path.write_bytes(b"same bundled configuration")
    inode = (tmp_path / "personaplex/config.json").stat().st_ino
    assert migrate_weights(tmp_path) == 1
    assert not (tmp_path / "config.json").exists()
    assert (tmp_path / "personaplex/config.json").stat().st_ino == inode


def test_active_lfm3_blocks_migration_before_personaplex_moves(tmp_path):
    legacy = tmp_path / "providers/lfm3-audio"
    (tmp_path / "model.safetensors").write_bytes(b"keep model")
    with assets.asset_lock(legacy):
        with pytest.raises(RuntimeError, match="in use"):
            migrate_weights(tmp_path)
    assert (tmp_path / "model.safetensors").read_bytes() == b"keep model"
    assert not (tmp_path / "personaplex").exists()
    assert legacy.exists()


def test_lfm3_migration_accepts_empty_destination_and_keeps_unrelated_providers(tmp_path):
    legacy = tmp_path / "providers/lfm3-audio"
    legacy.mkdir(parents=True)
    (legacy / "installed.json").write_bytes(b"inventory")
    (tmp_path / "providers/other-provider").mkdir()
    (tmp_path / "lfm3-audio").mkdir()
    assert migrate_weights(tmp_path) == 1
    assert (tmp_path / "lfm3-audio/installed.json").read_bytes() == b"inventory"
    assert (tmp_path / "providers/other-provider").is_dir()


def test_migration_rejects_symlinked_provider_directory(tmp_path):
    weights, outside = tmp_path / "weights", tmp_path / "outside"
    weights.mkdir()
    outside.mkdir()
    (weights / "model.safetensors").write_bytes(b"keep model")
    (weights / "personaplex").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real provider directories"):
        migrate_weights(weights)
    assert (weights / "model.safetensors").read_bytes() == b"keep model"
    assert not list(outside.iterdir())


def test_missing_weights_root_is_not_created_by_migration_or_catalogue(tmp_path):
    weights = tmp_path / "absent"
    assert migrate_weights(weights) == 0
    config = ControllerConfig(weights_dir=weights, data_dir=tmp_path / "data")
    for name in ("personaplex", "lfm3-audio"):
        assert provider(name).assets_dir(config) == weights / name
        assert provider(name).describe(config)["availability"] == "preparation_required"
    assert not weights.exists()


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_personaplex_preparation_and_check_use_shared_root(tmp_path, monkeypatch, override, check):
    config_path = tmp_path / "controller.json"
    config = ControllerConfig(weights_dir=tmp_path / "configured", data_dir=tmp_path)
    save_config(config_path, config)
    calls = []

    def prepare(root, revision=None):
        calls.append(root)
        return {"revision": "a" * 40}

    monkeypatch.setattr(model_files, "prepare_model", prepare)
    monkeypatch.setattr(model_files, "verify_model", prepare)
    arguments = ["nox-voice", "--config", str(config_path), "prepare-model"]
    weights = tmp_path / "override" if override else config.weights_dir
    if override:
        arguments += ["--weights-dir", str(weights)]
    if check:
        arguments += ["--check"]
    monkeypatch.setattr(sys, "argv", arguments)
    cli.main()
    assert calls == [weights / "personaplex"]
    assert not weights.exists()


def test_cli_migration_uses_configured_root_without_changing_settings(tmp_path, monkeypatch):
    config_path = tmp_path / "controller.json"
    weights = tmp_path / "weights"
    weights.mkdir()
    config = ControllerConfig(weights_dir=weights, data_dir=tmp_path)
    save_config(config_path, config)
    original = config_path.read_bytes()
    (weights / "model.safetensors").write_bytes(b"model")
    monkeypatch.setattr(
        sys, "argv", ["nox-voice", "--config", str(config_path), "migrate-weights"]
    )
    cli.main()
    assert (weights / "personaplex/model.safetensors").read_bytes() == b"model"
    assert config_path.read_bytes() == original
