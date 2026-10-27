"""Fixed-destination, certificate-verified transport to the Mac controller."""

import ssl
from pathlib import Path

import httpx2 as httpx
from websockets.asyncio.client import connect

from .config import Config


class ControllerUnavailable(Exception):
    pass


def readable_certificate(path: Path, label: str) -> None:
    """Check access as the running gateway user without exposing file contents."""
    try:
        with path.open("rb"):
            pass
    except FileNotFoundError:
        raise ControllerUnavailable(
            f"{label} is missing: {path}. Check the Docker secret mount."
        ) from None
    except PermissionError:
        raise ControllerUnavailable(
            f"{label} is not readable: {path} (permission denied). "
            "Check the secret file permissions for the container user."
        ) from None
    except OSError:
        raise ControllerUnavailable(
            f"{label} cannot be opened as a file: {path}. Check the Docker secret mount."
        ) from None


class Controller:
    def __init__(self, config: Config):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._ssl: ssl.SSLContext | None = None

    def context(self) -> ssl.SSLContext:
        if self._ssl is None:
            readable_certificate(self.config.ca_file, "Controller CA certificate")
            try:
                context = ssl.create_default_context(cafile=str(self.config.ca_file))
            except (OSError, ssl.SSLError):
                raise ControllerUnavailable(
                    f"Controller CA certificate is invalid: {self.config.ca_file}. "
                    "Mount the ca.crt file from the Mac gateway bundle in PEM format."
                ) from None
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            readable_certificate(self.config.cert_file, "Controller client certificate")
            readable_certificate(self.config.key_file, "Controller client private key")
            try:
                # Setup generates an unencrypted key. Never prompt for a password in the server.
                context.load_cert_chain(
                    str(self.config.cert_file), str(self.config.key_file), password=""
                )
            except ssl.SSLError as error:
                if error.reason == "KEY_VALUES_MISMATCH":
                    message = "Controller client certificate and private key do not match."
                else:
                    message = "Controller client certificate or private key is invalid."
                raise ControllerUnavailable(
                    f"{message} Mount client.crt and the unencrypted client.key "
                    "from the same Mac gateway bundle."
                ) from None
            except OSError:
                raise ControllerUnavailable(
                    "Controller client certificate or private key cannot be loaded. "
                    "Check the Docker secret mounts and file permissions."
                ) from None
            self._ssl = context
        return self._ssl

    async def request(self, method: str, path: str, payload=None, timeout=8) -> tuple[int, dict]:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.controller_url,
                verify=self.context(),
                timeout=8,
                trust_env=False,
            )
        try:
            response = await self._client.request(method, path, json=payload, timeout=timeout)
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Unexpected controller response")
            return response.status_code, value
        except (httpx.HTTPError, ValueError):
            raise ControllerUnavailable(
                "Cannot reach the Mac controller. Check the Mac, address and certificates."
            ) from None

    def conversation(self):
        return connect(
            self.config.controller_url.replace("https://", "wss://", 1) + "/ws",
            ssl=self.context(),
            max_size=8192,
            max_queue=4,
            write_limit=32768,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=10,
            proxy=None,
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
