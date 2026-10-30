import hashlib
import io
import json
import tarfile

import pytest
from engine.lfm3 import assets


def archive_at(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, value in entries:
            entry = tarfile.TarInfo(name)
            entry.mode = 0o755
            if isinstance(value, tuple):
                entry.type, entry.linkname = value
                archive.addfile(entry)
            else:
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    archive = tmp_path / "pinned.tar.gz"
    archive_at(
        archive,
        [
            ("bin/llama-server", b"test executable"),
            ("lib/test.dylib", b"test library"),
            ("bin/test.dylib", (tarfile.SYMTYPE, "../lib/test.dylib")),
        ],
    )
    contents = [b"test model", b"test projector"]
    data = {
        "runtime": {
            "url": "https://example.invalid/pinned",
            "size": archive.stat().st_size,
            "sha256": assets.digest(archive),
        },
        "model": {
            "repository": "test/model",
            "revision": "pinned",
            "files": [
                {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                for name, value in zip(["model.gguf", "projector.gguf"], contents)
            ],
        },
    }
    monkeypatch.setattr(assets, "manifest", lambda: data)
    monkeypatch.setattr(assets.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(assets.platform, "machine", lambda: "arm64")
    source = tmp_path / "explicit-source"
    (source / "models").mkdir(parents=True)
    for item, value in zip(data["model"]["files"], contents):
        (source / "models" / item["name"]).write_bytes(value)
    downloads = []

    def download(url, destination, expected):
        downloads.append(url)
        assert expected == data["runtime"]
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(assets, "download", download)
    return tmp_path / "managed", source, downloads


def test_explicit_import_verifies_copies_and_keeps_all_generated_files_contained(installation):
    root, source, downloads = installation
    assets.prepare(root, source)
    binary, models = assets.verify(root)
    assert binary.is_relative_to(root) and binary.name == "llama-server"
    assert len(downloads) == 1  # Only the pinned runtime; no model download.
    assert all(path.read_bytes() == (source / "models" / path.name).read_bytes() for path in models)
    assert all(
        path.stat().st_ino != (source / "models" / path.name).stat().st_ino for path in models
    )
    assert (root / "runtime/bin/test.dylib").is_symlink()
    assert not list((root / ".tmp").iterdir())
    assert (root / "installed.json").stat().st_mode & 0o077 == 0
    assert not assets.missing_assets(root)
    models[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="model files"):
        assets.verify(root)


def test_invalid_explicit_source_is_rejected_before_network_or_runtime_changes(installation):
    root, source, downloads = installation
    (source / "models/model.gguf").write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="source model"):
        assets.prepare(root, source)
    assert downloads == []
    assert not (root / "runtime").exists()


def test_read_only_availability_does_not_create_directories_or_download(installation):
    root, _, downloads = installation
    assert assets.missing_assets(root)
    assert not root.exists() and downloads == []


def test_runtime_mutation_is_detected_and_running_assets_cannot_be_prepared(installation):
    root, source, downloads = installation
    assets.prepare(root, source)
    with assets.asset_lock(root):
        with pytest.raises(RuntimeError, match="in use"):
            assets.prepare(root, source)
    assert len(downloads) == 1
    (root / "runtime/lib/test.dylib").write_bytes(b"changed library")
    with pytest.raises(ValueError, match="runtime"):
        assets.verify(root)


@pytest.mark.parametrize(
    "entry",
    [
        ("../escaped", b"bad"),
        ("/absolute", b"bad"),
        ("bad", (tarfile.SYMTYPE, "../../escaped")),
        ("bad", (tarfile.LNKTYPE, "inside")),
        ("bad", (tarfile.CHRTYPE, "")),
    ],
)
def test_untrusted_archive_cannot_escape_or_create_special_files(tmp_path, entry):
    archive = tmp_path / "unsafe.tar.gz"
    archive_at(archive, [("normal", b"must not write partial archives"), entry])
    target = tmp_path / "target"
    with pytest.raises(ValueError):
        assets.extract_runtime(archive, target)
    assert not target.exists()


def test_existing_escape_symlink_is_not_followed(installation, tmp_path):
    root, source, downloads = installation
    root.mkdir()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (root / ".tmp").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        assets.prepare(root, source)
    assert downloads == [] and list(outside.iterdir()) == []


def test_explicit_source_does_not_follow_a_model_link_outside_its_directory(installation, tmp_path):
    root, source, downloads = installation
    original = source / "models/model.gguf"
    unrelated = tmp_path / "unrelated.gguf"
    original.rename(unrelated)
    original.symlink_to(unrelated)
    with pytest.raises(ValueError, match="escapes"):
        assets.prepare(root, source)
    assert downloads == []
    assert unrelated.read_bytes() == b"test model"


def test_corrupted_install_manifest_fails_without_repairing_or_overwriting(installation):
    root, source, _ = installation
    assets.prepare(root, source)
    path = root / "installed.json"
    path.write_text(json.dumps(["invalid schema"]))
    with pytest.raises(ValueError, match="manifest"):
        assets.verify(root)
    assert path.read_text() == '["invalid schema"]'
