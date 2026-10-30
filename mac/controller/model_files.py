"""Prepare only the official, pinned PersonaPlex assets; never execute downloaded code."""

import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .config import VOICE_NAMES, write_private

REPOSITORY = "nvidia/personaplex-7b-v1"
ASSETS = (
    "model.safetensors",
    "tokenizer-e351c8d8-checkpoint125.safetensors",
    "tokenizer_spm_32k_3.model",
    "voices.tgz",
)


def asset_root(weights_dir: Path) -> Path:
    return weights_dir / "personaplex"


def artifact_manifest() -> dict:
    return json.loads(Path(__file__).with_name("personaplex-artifacts.json").read_text())


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def missing_files(directory: Path) -> list[str]:
    required = [*ASSETS, *[f"voices_cpu/{name}.pt" for name in VOICE_NAMES], "manifest.json"]
    return [name for name in required if not (directory / name).is_file()]


def extract_voices(archive: Path, destination: Path) -> None:
    """Reject traversal, duplicate members, links, unexpected files and archive bombs."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = {f"{voice}.pt" for voice in VOICE_NAMES}
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)
        found: set[str] = set()
        total = 0
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                    raise ValueError("Unsafe path in voice archive.")
                if member.isdir() and str(path) in {"voices", "."}:
                    continue
                if (
                    not member.isfile()
                    or path.name not in expected
                    or len(path.parts) > 2
                    or (len(path.parts) == 2 and path.parts[0] != "voices")
                ):
                    raise ValueError("Unexpected entry in voice archive.")
                if path.name in found or member.size <= 0 or member.size > 32 * 1024 * 1024:
                    raise ValueError("Invalid or duplicate voice file.")
                total += member.size
                if total > 128 * 1024 * 1024:
                    raise ValueError("Voice archive exceeds the extraction size limit.")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("Unreadable voice file.")
                content = source.read(member.size + 1)
                if len(content) != member.size:
                    raise ValueError("Truncated voice file.")
                write_private(staging / path.name, content)
                found.add(path.name)
        if found != expected:
            raise ValueError("Voice archive does not contain all 18 expected voices.")
        destination.mkdir(parents=True, exist_ok=True)
        for name in expected:
            (staging / name).replace(destination / name)


def verify_model(directory: Path) -> dict:
    missing = missing_files(directory)
    if missing:
        raise ValueError("Model files are missing. Run 'nox-voice prepare-model' first.")
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("repository") != REPOSITORY or not re.fullmatch(
        r"[a-f0-9]{40}", manifest.get("revision", "")
    ):
        raise ValueError("Invalid model manifest.")
    expected = set(ASSETS) | {f"voices_cpu/{voice}.pt" for voice in VOICE_NAMES}
    files = manifest.get("files", {})
    if set(files) != expected:
        raise ValueError("Incomplete model manifest. Run model preparation again.")
    for name, expected_hash in files.items():
        if (
            not re.fullmatch(r"[a-f0-9]{64}", str(expected_hash))
            or digest(directory / name) != expected_hash
        ):
            raise ValueError(f"Integrity check failed for {name}. Run model preparation again.")
    return manifest


def prepare_model(directory: Path, revision: str | None = None) -> dict:
    from huggingface_hub import HfApi, get_token, hf_hub_download

    if revision and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Use a full 40-character official commit hash for --revision.")
    revision = revision or artifact_manifest()["revision"]
    token = get_token()
    info = HfApi().model_info(REPOSITORY, revision=revision, token=token, files_metadata=True)
    if not info.sha or not re.fullmatch(r"[a-f0-9]{40}", info.sha):
        raise ValueError("The model repository did not return an immutable revision.")
    metadata = {item.rfilename: item for item in info.siblings}
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ASSETS:
        print(f"Downloading and verifying {name}...", flush=True)
        path = Path(
            hf_hub_download(REPOSITORY, name, revision=info.sha, token=token, local_dir=directory)
        )
        actual = digest(path)
        record = metadata[name]
        lfs = record.lfs
        sha256 = (
            (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None))
            if lfs
            else None
        )
        if sha256:
            valid = actual == sha256
        else:
            # Small Git blobs use Git's object hash instead of an LFS SHA-256.
            content = path.read_bytes()
            valid = (
                hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                == record.blob_id
            )
        if not valid:
            raise ValueError(f"Official checksum mismatch for {name}.")
        hashes[name] = actual
    extract_voices(directory / "voices.tgz", directory / "voices_cpu")
    for name in VOICE_NAMES:
        relative = f"voices_cpu/{name}.pt"
        hashes[relative] = digest(directory / relative)
    manifest = {"repository": REPOSITORY, "revision": info.sha, "files": hashes}
    write_private(directory / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest
