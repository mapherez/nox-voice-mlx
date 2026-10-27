"""Deployment-only settings. No controller address or credentials come from the browser."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def secret(name: str) -> str:
    filename = os.environ.get(name + "_FILE")
    return Path(filename).read_text().strip() if filename else os.environ.get(name, "").strip()


@dataclass(frozen=True)
class Config:
    public_url: str
    owner_email: str
    google_client_id: str
    google_client_secret: str
    session_secret: str
    controller_url: str
    ca_file: Path
    cert_file: Path
    key_file: Path
    data_dir: Path
    static_dir: Path

    def __post_init__(self):
        public = urlsplit(self.public_url)
        controller = urlsplit(self.controller_url)
        for value in (public, controller):
            if (
                not value.hostname
                or value.username
                or value.password
                or value.query
                or value.fragment
                or value.path not in ("", "/")
            ):
                raise ValueError(
                    "Webapp and controller URLs must be origins without credentials, paths or queries."
                )
            if value.port is not None and not 1 <= value.port <= 65535:
                raise ValueError("URL ports must be between 1 and 65535.")
        if controller.scheme != "https":
            raise ValueError("The Mac controller URL must use HTTPS.")
        if public.scheme != "https" and not (
            public.scheme == "http" and public.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("The webapp URL must use HTTPS, except HTTP on localhost.")
        if len(self.session_secret) < 32:
            raise ValueError(
                "NOX_VOICE_SESSION_SECRET must contain at least 32 characters (or use its _FILE variable)."
            )
        if not self.owner_email or "@" not in self.owner_email:
            raise ValueError("Configure NOX_VOICE_OWNER_EMAIL before exposing the webapp.")

    @property
    def secure_cookies(self) -> bool:
        return urlsplit(self.public_url).scheme == "https"

    @classmethod
    def from_env(cls):
        return cls(
            public_url=os.environ.get("NOX_VOICE_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
            owner_email=os.environ.get("NOX_VOICE_OWNER_EMAIL", "").strip().casefold(),
            google_client_id=os.environ.get("NOX_VOICE_GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=secret("NOX_VOICE_GOOGLE_CLIENT_SECRET"),
            session_secret=secret("NOX_VOICE_SESSION_SECRET"),
            controller_url=os.environ.get(
                "NOX_VOICE_CONTROLLER_URL", "https://127.0.0.1:8443"
            ).rstrip("/"),
            ca_file=Path(
                os.environ.get("NOX_VOICE_CONTROLLER_CA_FILE", "/run/secrets/controller_ca")
            ),
            cert_file=Path(
                os.environ.get(
                    "NOX_VOICE_CONTROLLER_CERT_FILE", "/run/secrets/controller_client_cert"
                )
            ),
            key_file=Path(
                os.environ.get(
                    "NOX_VOICE_CONTROLLER_KEY_FILE", "/run/secrets/controller_client_key"
                )
            ),
            data_dir=Path(os.environ.get("NOX_VOICE_DATA_DIR", "/data")),
            static_dir=Path(
                os.environ.get(
                    "NOX_VOICE_STATIC_DIR",
                    str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
                )
            ),
        )
