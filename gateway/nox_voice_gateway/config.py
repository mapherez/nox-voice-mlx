"""Deployment-only settings. No controller address or credentials come from the browser."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def secret(name: str) -> str:
    filename = os.environ.get(name + "_FILE")
    return Path(filename).read_text().strip() if filename else os.environ.get(name, "").strip()


def normalize_origin(value: str, *, allow_loopback_http: bool = True) -> str:
    parsed = urlsplit(value.strip())
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("URLs must be origins without credentials, paths or queries.")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if not re.fullmatch(r"[a-z0-9._:\-]+", host):
        raise ValueError("Invalid origin hostname.")
    if parsed.scheme != "https" and not (
        allow_loopback_http
        and parsed.scheme == "http"
        and host in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("HTTPS is required, except HTTP on localhost for the webapp.")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL ports must be between 1 and 65535.")
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


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
    web_port: int = 8080
    additional_origins: tuple[str, ...] = ()

    def __post_init__(self):
        normalize_origin(self.controller_url, allow_loopback_http=False)
        if not 1 <= self.web_port <= 65535:
            raise ValueError("NOX_VOICE_WEB_PORT must be between 1 and 65535.")
        authorities = {}
        for origin in self.browser_origins:
            parsed = urlsplit(origin)
            key = (parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            if key in authorities and authorities[key] != parsed.scheme:
                raise ValueError("Each browser host and port must use a single scheme.")
            authorities[key] = parsed.scheme
        if len(self.session_secret) < 32:
            raise ValueError(
                "NOX_VOICE_SESSION_SECRET must contain at least 32 characters (or use its _FILE variable)."
            )
        if not self.owner_email or "@" not in self.owner_email:
            raise ValueError("Configure NOX_VOICE_OWNER_EMAIL before exposing the webapp.")

    @property
    def browser_origins(self) -> tuple[str, ...]:
        values = [
            f"http://localhost:{self.web_port}",
            f"http://127.0.0.1:{self.web_port}",
            f"http://[::1]:{self.web_port}",
        ]
        if self.public_url.strip():
            values.append(self.public_url)
        values.extend(self.additional_origins)
        return tuple(dict.fromkeys(normalize_origin(value) for value in values))

    def origin_for_host(self, host: str) -> str | None:
        for origin in self.browser_origins:
            parsed = urlsplit(origin)
            aliases = {parsed.netloc}
            if parsed.port is None:
                aliases.add(f"{parsed.netloc}:{443 if parsed.scheme == 'https' else 80}")
            if host.lower() in aliases:
                return origin
        return None

    @classmethod
    def from_env(cls):
        return cls(
            public_url=os.environ.get("NOX_VOICE_PUBLIC_URL", "").strip().rstrip("/"),
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
            web_port=int(os.environ.get("NOX_VOICE_WEB_PORT", "8080")),
            additional_origins=tuple(
                value.strip()
                for value in os.environ.get("NOX_VOICE_ALLOWED_ORIGINS", "").split(",")
                if value.strip()
            ),
        )
