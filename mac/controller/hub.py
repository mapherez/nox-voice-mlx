"""Fixed Hugging Face endpoints and resumable, integrity-checked artifact downloads."""

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import write_private

HUB = "https://huggingface.co"


class DownloadError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if urlsplit(url).scheme != "https":
            raise DownloadError("NETWORK", "The download server requested an insecure redirect.")
        redirected = super().redirect_request(request, fp, code, message, headers, url)
        if redirected is not None and urlsplit(request.full_url).netloc != urlsplit(url).netloc:
            redirected.remove_header("Authorization")
        return redirected


def request(url, token=None, headers=None, method=None):
    values = {"User-Agent": "NoX-Voice/1", **(headers or {})}
    if token and urlsplit(url).netloc == "huggingface.co":
        values["Authorization"] = f"Bearer {token}"
    try:
        return build_opener(ProxyHandler({}), SafeRedirect()).open(
            Request(url, headers=values, method=method), timeout=30
        )
    except HTTPError as error:
        if error.code in {401, 403}:
            raise DownloadError(
                "MODEL_ACCESS", "Access denied. Check the Hugging Face credential and accept the model's access terms."
            ) from None
        if error.code == 429:
            raise DownloadError("RATE_LIMIT", "Hugging Face rate limit reached. Retry later.") from None
        raise DownloadError("NETWORK", "The model source could not complete the request. Retry later.") from None
    except (OSError, URLError):
        raise DownloadError("NETWORK", "Cannot reach the model source. Check the Mac's network connection.") from None


def hub_json(path, token):
    with request(HUB + path, token) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise DownloadError("METADATA", "The model source returned oversized metadata.")
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise DownloadError("METADATA", "The model source returned invalid metadata. Retry later.") from None


def valid_revision(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value) is not None


def checksum(path: Path, expected: dict) -> bool:
    if not path.is_file() or path.stat().st_size != expected["size"]:
        return False
    if expected.get("sha256"):
        digest = hashlib.sha256()
    else:
        digest = hashlib.sha1(f"blob {expected['size']}\0".encode())
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest() == (expected.get("sha256") or expected["blobId"])


def download(url, destination, expected, progress, token=None):
    """Resume only an immutable, checksummed artifact. Keep partials on interruption."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    identity_file = destination.with_name(destination.name + ".part.json")
    if destination.is_symlink() or partial.is_symlink() or identity_file.is_symlink():
        raise DownloadError("FILES", "A model download path is a symlink. Check the model directory.")
    size = expected["size"]
    identity = {key: expected.get(key) for key in ("size", "sha256", "blobId")}
    if destination.is_file() and destination.stat().st_size == size:
        progress(size, 0, "verifying")
    if checksum(destination, expected):
        partial.unlink(missing_ok=True)
        identity_file.unlink(missing_ok=True)
        progress(size, 0, "downloaded")
        return
    previous_identity = None
    if identity_file.is_file():
        try:
            previous_identity = json.loads(identity_file.read_text())
        except (ValueError, OSError):
            pass
    if partial.exists() and previous_identity != identity:
        partial.unlink()  # Never append bytes from a different artifact/revision.
    write_private(identity_file, json.dumps(identity))
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size or (offset == size and not checksum(partial, expected)):
        partial.unlink()
        offset = 0
    progress(offset, 0, "downloading")
    if offset < size:
        if shutil.disk_usage(destination.parent).free < size - offset + 64 * 1024 * 1024:
            raise DownloadError("DISK_SPACE", "Not enough free disk space on the Mac for this download.")
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with request(url, token, headers) as response:
            if offset:
                if response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset or int(match[3]) != size:
                        raise DownloadError("RANGE", "The source returned an invalid partial download. Retry the download.")
                elif response.status == 200:
                    offset = 0  # Range not supported: explicitly restart this file.
                else:
                    raise DownloadError("RANGE", "The source cannot resume this download.")
            started, last, received = time.monotonic(), 0.0, offset
            with partial.open("ab" if offset else "wb") as output:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > size:
                        raise DownloadError("INTEGRITY", "A download exceeded its expected size.")
                    output.write(block)
                    now = time.monotonic()
                    if now - last >= 0.25:
                        progress(received, (received - offset) / max(now - started, 0.001), "downloading")
                        last = now
        if received != size:
            raise DownloadError("NETWORK", "The download was interrupted. Retry to resume it.")
    progress(size, 0, "verifying")
    if not checksum(partial, expected):
        partial.unlink(missing_ok=True)
        raise DownloadError("INTEGRITY", "A file failed its integrity check. Retry to download it again.")
    partial.replace(destination)
    identity_file.unlink(missing_ok=True)
    progress(size, 0, "downloaded")
