"""Pinned downloads, isolated assets and offline verification for the LFM3 provider."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.request import ProxyHandler, Request, build_opener

from controller.config import write_private


def manifest() -> dict:
    return json.loads(Path(__file__).with_name("artifacts.json").read_text())


def asset_root(weights_dir: Path) -> Path:
    return weights_dir / "lfm3-audio"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            result.update(block)
    return result.hexdigest()


def checked(path: Path, expected: dict) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected["size"]
        and digest(path) == expected["sha256"]
    )


def contained(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts or "\\" in name:
        raise ValueError("Invalid provider asset path.")
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Provider asset escapes its configured directory.")
    return path


@contextmanager
def asset_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contained(root, ".lock").open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("These LFM3 assets are in use or being prepared.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def download(url: str, destination: Path, expected: dict) -> None:
    if checked(destination, expected):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        opener = build_opener(ProxyHandler({}))
        request = Request(url, headers={"User-Agent": "NoX-Voice-Assets/1"})
        with opener.open(request, timeout=30) as response, partial.open("wb") as output:
            count = 0
            while block := response.read(1024 * 1024):
                count += len(block)
                if count > expected["size"]:
                    raise ValueError("Download exceeds the pinned asset size.")
                output.write(block)
        if not checked(partial, expected):
            raise ValueError("Downloaded asset does not match its pinned SHA-256 and size.")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def extract_runtime(archive: Path, destination: Path) -> None:
    """Validate the full archive before writing; never write through a symlink."""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names, links = set(), set()
        if len(members) > 4096 or sum(member.size for member in members) > 256 * 1024 * 1024:
            raise ValueError("Runtime archive exceeds its extraction budget.")
        for member in members:
            name = str(PurePosixPath(member.name))
            if name in names:
                raise ValueError("Duplicate runtime archive entry.")
            names.add(name)
            target = contained(destination, member.name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("Unsupported runtime archive entry.")
            if member.issym():
                link = target.parent / member.linkname
                if Path(member.linkname).is_absolute() or not link.resolve().is_relative_to(
                    destination.resolve()
                ):
                    raise ValueError("Unsafe runtime archive symlink.")
                links.add(name)
        for member in members:
            if any(str(parent) in links for parent in PurePosixPath(member.name).parents):
                raise ValueError("Runtime archive entry writes through a symlink.")
        for member in members:
            target = contained(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content, target.open("wb") as output:
                    shutil.copyfileobj(content, output)
                target.chmod(member.mode & 0o755)
        for member in members:
            if member.issym():
                target = contained(destination, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)


def inventory(directory: Path) -> dict:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Runtime link escapes its directory.")
            files[path.relative_to(directory).as_posix()] = {"link": os.readlink(path)}
        elif path.is_file():
            files[path.relative_to(directory).as_posix()] = {
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
    return files


def missing_assets(root: Path) -> list[str]:
    required = [
        "installed.json",
        "runtime",
        *["models/" + item["name"] for item in manifest()["model"]["files"]],
    ]
    return [name for name in required if not contained(root, name).exists()]


def verify(root: Path) -> tuple[Path, list[Path]]:
    data = manifest()
    installed = json.loads(contained(root, "installed.json").read_text())
    if (
        not isinstance(installed, dict)
        or installed.get("schemaVersion") != 1
        or installed.get("artifacts") != data
    ):
        raise ValueError("LFM3 installation manifest does not match the pinned artifacts.")
    runtime = contained(root, "runtime")
    if not installed.get("runtimeFiles") or inventory(runtime) != installed["runtimeFiles"]:
        raise ValueError("LFM3 runtime is missing or has changed. Prepare it again.")
    if not isinstance(installed.get("binary"), str):
        raise ValueError("Missing LFM3 executable metadata.")
    binary = contained(runtime, installed["binary"])
    if binary.name != "llama-server" or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("LFM3 runtime executable is unavailable.")
    models = [contained(root, "models/" + item["name"]) for item in data["model"]["files"]]
    if not all(checked(path, expected) for path, expected in zip(models, data["model"]["files"])):
        raise ValueError("LFM3 model files are missing or invalid. Prepare them again.")
    return binary, models


def prepare(root: Path, source: Path | None = None) -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The pinned LFM3 runtime requires macOS on Apple Silicon.")
    with asset_lock(root):
        data = manifest()
        if source is not None:
            for item in data["model"]["files"]:
                if not checked(contained(root, "models/" + item["name"]), item) and not checked(
                    contained(source, "models/" + item["name"]), item
                ):
                    raise ValueError(
                        "The explicitly supplied source model does not match the pinned artifact."
                    )
        temporary = contained(root, ".tmp")
        temporary.mkdir(exist_ok=True)
        archive = contained(root, ".tmp/runtime.tar.gz")
        contained(root, ".tmp/runtime.tar.gz.part")
        print(json.dumps({"phase": "runtime"}), flush=True)
        try:
            download(data["runtime"]["url"], archive, data["runtime"])
            with tempfile.TemporaryDirectory(prefix="extract-", dir=temporary) as folder:
                staging = Path(folder)
                extract_runtime(archive, staging)
                binaries = list(staging.rglob("llama-server"))
                if len(binaries) != 1:
                    raise ValueError("Expected exactly one pinned runtime executable.")
                runtime_files = inventory(staging)
                binary_name = binaries[0].relative_to(staging).as_posix()
                target = contained(root, "runtime")
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(staging, target, symlinks=True)
        finally:
            archive.unlink(missing_ok=True)
        print(json.dumps({"phase": "models"}), flush=True)
        for item in data["model"]["files"]:
            target = contained(root, "models/" + item["name"])
            if checked(target, item):
                continue
            if source is not None:
                original = contained(source, "models/" + item["name"])
                if not checked(original, item):
                    raise ValueError(
                        "The explicitly supplied source model does not match the pinned artifact."
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = contained(root, "models/" + item["name"] + ".part")
                try:
                    shutil.copyfile(original, partial)
                    if not checked(partial, item):
                        raise ValueError("Copied model verification failed.")
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            else:
                contained(root, "models/" + item["name"] + ".part")
                url = f"https://huggingface.co/{data['model']['repository']}/resolve/{data['model']['revision']}/{item['name']}"
                download(url, target, item)
        contained(root, "installed.json.tmp")
        write_private(
            contained(root, "installed.json"),
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifacts": data,
                    "binary": binary_name,
                    "runtimeFiles": runtime_files,
                },
                indent=2,
            )
            + "\n",
        )
        print(json.dumps({"phase": "ready"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    import signal

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        prepare(args.root)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (OSError, RuntimeError, ValueError, tarfile.TarError):
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
