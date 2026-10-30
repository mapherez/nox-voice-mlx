"""Bounded HTTP JSON/SSE. No redirects, proxy environment or automatic retries."""

import asyncio
import json

import httpx2 as httpx

MAX_BODY = 2 * 1024 * 1024
VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}


class ExecutionError(Exception):
    """Known local/protocol failure, with a content-free diagnostic."""


class OutcomeUnknown(ExecutionError):
    """Dispatch may have happened. Never retry this invocation."""


class RejectedCall(ExecutionError):
    """JSON-RPC explicitly rejected an invalid request before tool admission."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


async def json_http(client, method, url, *, body=None, headers=None, timeout=120):
    async with asyncio.timeout(timeout):
        async with client.stream(method, url, json=body, headers=headers, timeout=timeout) as response:
            if response.status_code >= 300:
                raise ExecutionError(f"Endpoint returned HTTP {response.status_code}.")
            if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise ExecutionError("Endpoint did not return JSON.")
            data = bytearray()
            async for packet in response.aiter_bytes():
                data.extend(packet)
                if len(data) > MAX_BODY:
                    raise ExecutionError("Endpoint response exceeds the 2 MiB payload limit.")
            try:
                value = json.loads(data)
                encode(value)
            except (ValueError, UnicodeError):
                raise ExecutionError("Endpoint returned invalid JSON.") from None
            if not isinstance(value, dict):
                raise ExecutionError("Endpoint returned an invalid response object.")
            return value


class MCPClient:
    def __init__(self, settings, secret=None, *, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self.owns_client = client is None
        self.headers = {"Accept": "application/json, text/event-stream"}
        if secret:
            self.headers["Authorization"] = "Bearer " + secret
        self.version = "2025-11-25"
        self.session = None
        self.counter = 0
        self.changed = False
        self.identity = None
        self.capabilities = {}

    async def message(self, message, request_id):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ExecutionError("Invalid MCP JSON-RPC envelope.")
        if "method" in message:
            if message["method"] == "notifications/tools/list_changed":
                self.changed = True
            if "id" in message:
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "Client feature not supported."}
                await self.post(reply)
            return None
        if type(message.get("id")) is not type(request_id) or message.get("id") != request_id:
            return None
        if "error" in message:
            # Remote error text can contain credentials or content; retain a safe code only.
            code = message["error"].get("code") if isinstance(message["error"], dict) else None
            error_type = RejectedCall if type(code) is int and code in {-32700, -32600, -32601, -32602} else ExecutionError
            raise error_type(f"MCP request rejected (code {code if type(code) is int else 'unknown'}).")
        if not isinstance(message.get("result"), dict):
            raise ExecutionError("MCP returned no valid result object.")
        return message["result"]

    async def post(self, message):
        headers = {**self.headers, "MCP-Protocol-Version": self.version}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        async with asyncio.timeout(self.settings.timeoutSeconds):
            async with self.client.stream("POST", self.settings.url, json=message,
                    headers=headers, timeout=self.settings.timeoutSeconds) as response:
                if response.status_code >= 300:
                    raise ExecutionError(f"MCP returned HTTP {response.status_code}. Reconnect to test access.")
                session = response.headers.get("mcp-session-id")
                if session is not None:
                    if not session or len(session) > 1024 or not session.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in session):
                        raise ExecutionError("MCP returned an invalid session identity.")
                    self.session = session
                if "id" not in message:
                    return None
                kind = response.headers.get("content-type", "").split(";", 1)[0]
                if kind == "application/json":
                    data = bytearray()
                    async for packet in response.aiter_bytes():
                        data.extend(packet)
                        if len(data) > MAX_BODY:
                            raise ExecutionError("MCP response exceeds the 2 MiB payload limit.")
                    return await self.message(json.loads(data), message["id"])
                if kind != "text/event-stream":
                    raise ExecutionError("MCP must return JSON or a Server-Sent Events stream.")
                size, buffer, parts = 0, "", []
                import codecs

                decoder = codecs.getincrementaldecoder("utf-8")()
                async for packet in response.aiter_bytes():
                    size += len(packet)
                    if size > MAX_BODY:
                        raise ExecutionError("MCP stream exceeds the 2 MiB payload limit.")
                    buffer += decoder.decode(packet)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")
                        if line.startswith("data:"):
                            parts.append(line[5:].removeprefix(" "))
                        elif not line:
                            if parts and "\n".join(parts).strip():
                                value = await self.message(json.loads("\n".join(parts)), message["id"])
                                if value is not None:
                                    return value
                            parts = []
                raise ExecutionError("MCP stream ended without a confirmed response. No automatic replay.")

    async def rpc(self, method, params=None):
        self.counter += 1
        message = {"jsonrpc": "2.0", "id": self.counter, "method": method}
        if params is not None:
            message["params"] = params
        try:
            return await self.post(message)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, RecursionError):
            raise ExecutionError("MCP connection failed or returned invalid data.") from None

    async def connect(self):
        try:
            return await self._connect()
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, RecursionError):
            raise ExecutionError("MCP initialization or discovery failed. No business tool was called.") from None

    async def _connect(self):
        result = await self.rpc("initialize", {"protocolVersion": self.version,
            "capabilities": {}, "clientInfo": {"name": "nox-voice", "version": "0.1.0"}})
        if (not result or result.get("protocolVersion") not in VERSIONS or not isinstance(result.get("serverInfo"), dict)
                or not isinstance(result["serverInfo"].get("name"), str) or not isinstance(result["serverInfo"].get("version"), str)):
            raise ExecutionError("MCP negotiated an unsupported protocol or server identity.")
        self.version, self.identity = result["protocolVersion"], result["serverInfo"]
        self.capabilities = result.get("capabilities", {})
        if not isinstance(self.capabilities, dict) or "tools" not in self.capabilities:
            raise ExecutionError("This MCP server does not advertise tools.")
        await self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, seen, cursor = [], set(), None
        while True:
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            if not page or not isinstance(page.get("tools"), list):
                raise ExecutionError("MCP returned an invalid catalogue.")
            tools.extend(page["tools"])
            if len(encode(tools).encode()) > MAX_BODY:
                raise ExecutionError("MCP catalogue exceeds the 2 MiB payload limit.")
            cursor = page.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ExecutionError("MCP returned an invalid pagination cursor.")
            seen.add(cursor)

    async def call(self, name, arguments, invocation_id):
        # The invocation ledger is in the service, never retried at transport level.
        try:
            return await self.rpc("tools/call", {"name": name, "arguments": arguments,
                "_meta": {"progressToken": invocation_id}})
        except RejectedCall:
            raise
        except ExecutionError:
            raise OutcomeUnknown("No confirmed tool result. The action may have completed; it will not be retried.") from None

    async def cancel(self):
        from contextlib import suppress

        with suppress(Exception):
            await asyncio.wait_for(self.post({"jsonrpc": "2.0", "method": "notifications/cancelled",
                "params": {"requestId": self.counter, "reason": "Conversation or task ended."}}), timeout=2)

    async def close(self):
        from contextlib import suppress

        if self.session:
            with suppress(Exception):
                await self.client.delete(self.settings.url, headers={**self.headers,
                    "Mcp-Session-Id": self.session, "MCP-Protocol-Version": self.version}, timeout=2)
            self.session = None
        if self.owns_client:
            await self.client.aclose()
