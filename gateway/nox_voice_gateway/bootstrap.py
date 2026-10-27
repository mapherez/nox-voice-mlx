"""Prepare read-only Docker secrets in memory, then run the unprivileged app."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from .transport import Controller, ControllerUnavailable

APP_UID = APP_GID = 10001
SECRET_VARIABLES = (
    "NOX_VOICE_GOOGLE_CLIENT_SECRET_FILE",
    "NOX_VOICE_SESSION_SECRET_FILE",
    "NOX_VOICE_CONTROLLER_CA_FILE",
    "NOX_VOICE_CONTROLLER_CERT_FILE",
    "NOX_VOICE_CONTROLLER_KEY_FILE",
)


def stage_secrets(environment: dict[str, str], directory: Path, uid: int, gid: int) -> None:
    """Copy only configured inputs; never chmod/chown their host mounts."""
    os.chmod(directory, 0o700)
    replacements = {}
    try:
        for variable in SECRET_VARIABLES:
            if not (source := environment.get(variable)):
                continue
            destination = directory / variable.lower()
            try:
                with Path(source).open("rb") as incoming, destination.open("xb") as outgoing:
                    os.fchmod(outgoing.fileno(), 0o600)
                    shutil.copyfileobj(incoming, outgoing)
                    os.fchown(outgoing.fileno(), uid, gid)
            except OSError as error:
                raise RuntimeError(
                    f"Cannot prepare {variable} from {source}: {error.strerror}. "
                    "Check that the Compose secret source is a readable file."
                ) from None
            replacements[variable] = str(destination)
        os.chown(directory, uid, gid)
    except Exception:
        shutil.rmtree(directory)
        raise
    environment.update(replacements)


def drop_privileges() -> None:
    os.setgroups([])
    os.setgid(APP_GID)
    os.setuid(APP_UID)
    if os.geteuid() != APP_UID or os.getegid() != APP_GID:
        raise RuntimeError("Cannot drop container startup privileges.")


def main() -> None:
    try:
        os.umask(0o077)
        if os.geteuid() == 0:
            directory = Path(tempfile.mkdtemp(prefix="nox-voice-", dir="/tmp"))
            stage_secrets(os.environ, directory, APP_UID, APP_GID)
            drop_privileges()

        # Validate locally before advertising a healthy webapp. No Mac connection is made.
        from .config import Config

        Controller(Config.from_env()).context()
    except (OSError, RuntimeError, ValueError, ControllerUnavailable) as error:
        print(f"NoX Voice startup failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    os.execvp("nox-voice-web", ["nox-voice-web"])


if __name__ == "__main__":
    main()
