"""Owned offline-install worker. Token arrives over stdin, never argv/environment."""

import argparse
import json
import re
import sys
from pathlib import Path

from engine.lfm3 import assets

from . import hub, model_files
from .config import VOICE_NAMES, write_private


def emit(**value):
    print(json.dumps(value), flush=True)


class Reporter:
    def __init__(self, files, token=None):
        self.files = [{"name": item["name"], "totalBytes": item["size"], "downloadedBytes": 0,
            "state": "pending"} for item in files]
        self.token = token
        emit(phase="downloading", files=self.files, totalBytes=sum(item["size"] for item in files))

    def download(self, url, destination, expected):
        file = next(item for item in self.files if item["name"] == Path(destination).name)

        def progress(count, speed, state):
            file.update(downloadedBytes=count, state=state)
            emit(phase="verifying" if state == "verifying" else "downloading", files=self.files,
                downloadedBytes=sum(item["downloadedBytes"] for item in self.files),
                totalBytes=sum(item["totalBytes"] for item in self.files), bytesPerSecond=speed,
                file=file["name"])

        hub.download(url, destination, expected, progress, self.token)


def personaplex(root, token):
    if not token:
        raise hub.DownloadError("CREDENTIAL_REQUIRED", "Add a Hugging Face token in Credentials to download PersonaPlex.")
    plan_path = assets.contained(root, ".download-plan.json")
    for name in [*model_files.ASSETS, "voices_cpu", "manifest.json"]:
        assets.contained(root, name)
    pinned = model_files.artifact_manifest()
    revision = pinned["revision"]
    if plan_path.is_file():
        revision = json.loads(plan_path.read_text()).get("revision")
        if not hub.valid_revision(revision):
            raise hub.DownloadError("METADATA", "Invalid saved download revision. Check the model directory.")
    metadata = hub.hub_json(f"/api/models/{model_files.REPOSITORY}/revision/{revision}?blobs=true", token)
    revision = metadata.get("sha")
    if not hub.valid_revision(revision):
        raise hub.DownloadError("METADATA", "The model source did not provide an immutable revision.")
    records = {item["rfilename"]: item for item in metadata["siblings"]}
    files = []
    for name in model_files.ASSETS:
        record = records[name]
        lfs = record.get("lfs") or {}
        size = lfs.get("size", record.get("size"))
        sha = lfs.get("sha256")
        blob = record.get("blobId")
        if not isinstance(size, int) or not 0 < size < 32 * 1024**3 or not (
            isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha)
            or isinstance(blob, str) and re.fullmatch(r"[a-f0-9]{40}", blob)
        ):
            raise hub.DownloadError("METADATA", "Missing size or checksum for an official model file.")
        files.append({"name": name, "size": size, "sha256": sha, "blobId": blob})
    if revision == pinned["revision"]:
        expected = {item["name"]: item for item in pinned["files"]}
        if any(item["size"] != expected[item["name"]]["size"] or item["sha256"] != expected[item["name"]]["sha256"] for item in files):
            raise hub.DownloadError("METADATA", "Official file metadata differs from the pinned model manifest. Download stopped.")
    write_private(plan_path, json.dumps({"revision": revision, "files": files}))
    reporter = Reporter(files, token)
    for item in files:
        reporter.download(f"{hub.HUB}/{model_files.REPOSITORY}/resolve/{revision}/{item['name']}", root / item["name"], item)
    emit(phase="extracting")
    model_files.extract_voices(root / "voices.tgz", root / "voices_cpu")
    emit(phase="verifying")
    hashes = {name: model_files.digest(root / name) for name in
        [*model_files.ASSETS, *[f"voices_cpu/{voice}.pt" for voice in VOICE_NAMES]]}
    write_private(root / "manifest.json", json.dumps({"repository": model_files.REPOSITORY,
        "revision": revision, "files": hashes}))
    model_files.verify_model(root)


def run(provider, root, token=None):
    if provider == "lfm3-audio":
        data = assets.manifest()
        files = [{"name": "runtime.tar.gz", **data["runtime"]}, *data["model"]["files"]]
        reporter = Reporter(files)
        assets.download = reporter.download
        extract_runtime = assets.extract_runtime

        def extract(archive, directory):
            emit(phase="extracting")
            extract_runtime(archive, directory)

        assets.extract_runtime = extract
        assets.prepare(root)
        emit(phase="verifying")
        assets.verify(root)
    else:
        with assets.asset_lock(root):
            personaplex(root, token)
    emit(phase="complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["personaplex", "lfm3-audio"], required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        bootstrap = json.loads(sys.stdin.readline(2048))
        run(args.provider, args.root, bootstrap.get("token"))
    except hub.DownloadError as error:
        emit(phase="error", code=error.code, error=str(error))
        sys.exit(1)
    except Exception:
        emit(phase="error", code="PREPARATION", error="Could not prepare this model. Check network access, free disk space and model files, then retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
