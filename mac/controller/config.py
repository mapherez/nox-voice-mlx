"""Configuration and constrained model settings shared with the worker."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VOICE_NAMES = tuple(
    [f"NATF{i}" for i in range(4)]
    + [f"NATM{i}" for i in range(4)]
    + [f"VARF{i}" for i in range(5)]
    + [f"VARM{i}" for i in range(5)]
)
MAC_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path.home() / "Library" / "Application Support" / "NoX Voice"
DEFAULT_PROMPT = "You are a helpful AI assistant. Be concise and friendly."


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: Literal[
        "NATF0",
        "NATF1",
        "NATF2",
        "NATF3",
        "NATM0",
        "NATM1",
        "NATM2",
        "NATM3",
        "VARF0",
        "VARF1",
        "VARF2",
        "VARF3",
        "VARF4",
        "VARM0",
        "VARM1",
        "VARM2",
        "VARM3",
        "VARM4",
    ] = "NATF0"
    prompt: str = Field(default=DEFAULT_PROMPT, max_length=4096)
    bits: Literal[0, 4, 5, 6, 8] = 4


class ControllerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=8443, ge=1024, le=65535)
    worker_port: int = Field(default=8000, ge=1024, le=65535)
    weights_dir: Path = MAC_ROOT / "weights"
    data_dir: Path = DATA_DIR
    startup_timeout: float = Field(default=300, ge=1, le=1800)

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "voice-settings.json"

    @property
    def tls_dir(self) -> Path:
        return self.data_dir / "tls"

    def load_settings(self) -> VoiceSettings:
        if self.settings_file.exists():
            return VoiceSettings.model_validate_json(self.settings_file.read_text())
        return VoiceSettings()


def write_private(path: Path, data: str | bytes) -> None:
    """Atomically replace a private file without a permissive intermediate."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)
    temporary.replace(path)


def load_config(path: Path) -> ControllerConfig:
    return ControllerConfig.model_validate_json(path.read_text())


def save_config(path: Path, config: ControllerConfig) -> None:
    write_private(path, json.dumps(config.model_dump(mode="json"), indent=2) + "\n")
