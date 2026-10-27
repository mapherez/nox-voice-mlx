import io
import json
import tarfile

import pytest
from controller.config import VOICE_NAMES
from controller.model_files import ASSETS, REPOSITORY, digest, extract_voices, verify_model


def archive(path, entries):
    with tarfile.open(path, "w:gz") as bundle:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.type = kind
            if kind == tarfile.SYMTYPE:
                member.linkname = "/tmp/outside.pt"
            bundle.addfile(member, io.BytesIO(data) if member.isfile() else None)


@pytest.mark.parametrize(
    "entry",
    [
        ("../NATF0.pt", b"x", tarfile.REGTYPE),
        ("/NATF0.pt", b"x", tarfile.REGTYPE),
        ("voices/NATF0.pt", b"", tarfile.SYMTYPE),
        ("voices/unknown.pt", b"x", tarfile.REGTYPE),
        ("other/NATF0.pt", b"x", tarfile.REGTYPE),
    ],
)
def test_voice_archive_cannot_escape_or_load_unknown_entries(tmp_path, entry):
    source = tmp_path / "voices.tgz"
    archive(source, [entry])
    destination = tmp_path / "voices_cpu"
    with pytest.raises(ValueError):
        extract_voices(source, destination)
    assert not destination.exists()


def test_voice_archive_requires_the_complete_known_set(tmp_path):
    source = tmp_path / "voices.tgz"
    archive(
        source,
        [(f"voices/{name}.pt", b"tensor-placeholder", tarfile.REGTYPE) for name in VOICE_NAMES],
    )
    extract_voices(source, tmp_path / "voices_cpu")
    assert len(list((tmp_path / "voices_cpu").glob("*.pt"))) == 18


def test_duplicate_voice_entry_is_rejected(tmp_path):
    source = tmp_path / "voices.tgz"
    archive(source, [("voices/NATF0.pt", b"x", tarfile.REGTYPE)] * 2)
    with pytest.raises(ValueError, match="duplicate"):
        extract_voices(source, tmp_path / "voices_cpu")


def test_modified_model_or_voice_is_detected(tmp_path):
    names = [*ASSETS, *[f"voices_cpu/{voice}.pt" for voice in VOICE_NAMES]]
    checksums = {}
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"verified contents")
        checksums[name] = digest(path)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"repository": REPOSITORY, "revision": "a" * 40, "files": checksums})
    )
    assert verify_model(tmp_path)["repository"] == REPOSITORY
    (tmp_path / "voices_cpu/NATF0.pt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Integrity"):
        verify_model(tmp_path)
