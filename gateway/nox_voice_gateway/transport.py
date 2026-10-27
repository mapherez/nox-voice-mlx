"""Fixed-destination, certificate-verified transport to the Mac controller."""

import ssl

import httpx2 as httpx
from websockets.asyncio.client import connect

from .config import Config


class ControllerUnavailable(Exception):
    pass


class Controller:
    def __init__(self, config: Config):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._ssl: ssl.SSLContext | None = None

    def context(self) -> ssl.SSLContext:
        if self._ssl is None:
            try:
                context = ssl.create_default_context(cafile=str(self.config.ca_file))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(str(self.config.cert_file), str(self.config.key_file))
                self._ssl = context
            except (OSError, ssl.SSLError):
                raise ControllerUnavailable(
                    "Controller certificates are missing or invalid."
                ) from None
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
