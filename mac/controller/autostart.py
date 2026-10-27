"""Optional per-user launchd installation. No elevated service or model autostart."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import DATA_DIR, MAC_ROOT, write_private

LABEL = "com.nox.voice.controller"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_enabled() -> bool:
    return plist_path().exists()


def enable(config_file: Path) -> None:
    if sys.platform != "darwin":
        raise ValueError("Automatic startup is supported only on macOS.")
    if is_enabled():
        raise ValueError("Automatic startup is already enabled. Disable it before reinstalling.")
    document = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "controller.cli",
            "--config",
            str(config_file.resolve()),
            "controller",
        ],
        "WorkingDirectory": str(MAC_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(DATA_DIR / "controller.log"),
        "StandardErrorPath": str(DATA_DIR / "controller-error.log"),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(plist_path(), plistlib.dumps(document))
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path())], capture_output=True
    )
    if result.returncode:
        plist_path().unlink(missing_ok=True)
        raise ValueError(
            "Could not enable automatic startup. Stop the manual controller and try again after logging in."
        )


def disable() -> None:
    if not is_enabled():
        return
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    plist_path().unlink(missing_ok=True)
