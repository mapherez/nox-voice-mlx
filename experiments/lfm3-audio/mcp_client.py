"""In-memory MCP tools client: Streamable HTTP, JSON/SSE, no authentication or retries."""

import http.client
import json
import re
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit

VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
MAX_BODY = 2 * 1024 * 1024


class MCPError(RuntimeError):
    pass


class MCPTransportError(MCPError):
    """No confirmed tool result: never retry an execution automatically."""


def endpoint(value):
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() for c in value):
        raise MCPError("Enter a complete HTTP or HTTPS MCP URL.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise MCPError("Use an HTTP or HTTPS URL without credentials or a fragment.") from None
    return parsed, port


class MCPClient:
    def __init__(self, url, timeout=30):
        self.parsed, self.port = endpoint(url)
        self.url = url
        self.timeout = timeout
        self.version = "2025-11-25"
        self.session = None
        self.counter = 0
        self.name = "MCP server"
        self.tools = []
        self.aliases = {}

    def _connection(self, timeout=None):
        implementation = http.client.HTTPSConnection if self.parsed.scheme == "https" else http.client.HTTPConnection
        return implementation(self.parsed.hostname, self.port, timeout=timeout or self.timeout)

    def _path(self):
        return (self.parsed.path or "/") + ("?" + self.parsed.query if self.parsed.query else "")

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.version}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    def _server_message(self, message, request_id):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise MCPError("The server returned an invalid JSON-RPC message.")
        if "method" in message:
            if "id" in message:
                # Advertise no client features; still answer mandatory ping and unsupported requests.
                answer = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    answer["result"] = {}
                else:
                    answer["error"] = {"code": -32601, "message": "Client method not supported."}
                self._post(answer, None, allow_server_requests=False)
            return None
        if type(message.get("id")) is not type(request_id) or message.get("id") != request_id:
            return None
        if "error" in message:
            error = message["error"]
            raise MCPError(str(error.get("message", "MCP request failed.")) if isinstance(error, dict) else "MCP request failed.")
        if "result" not in message:
            raise MCPError("The server response has no result.")
        return message

    def _post(self, message, request_id, allow_server_requests=True):
        connection = self._connection()
        deadline = time.monotonic() + self.timeout
        timer = None
        try:
            connection.request("POST", self._path(), json.dumps(message).encode(), self._headers())
            active_socket = connection.sock

            def expire():
                if active_socket is not None:
                    try:
                        active_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            timer = threading.Timer(max(.01, deadline - time.monotonic()), expire)
            timer.daemon = True
            timer.start()
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                if response.status in {401, 403}:
                    raise MCPError("The MCP server refused access. This experiment supports unauthenticated endpoints only.")
                if response.status == 404 and self.session:
                    raise MCPTransportError("The MCP session expired or the endpoint is unavailable. Connect MCP again.")
                raise MCPTransportError("MCP returned HTTP {}. No execution will be retried automatically.".format(response.status))
            session = response.getheader("Mcp-Session-Id")
            if session is not None:
                if not session or len(session) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in session):
                    raise MCPError("The server returned an invalid session identifier.")
                self.session = session
            if request_id is None:
                response.read(MAX_BODY + 1)
                return None
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip()
            if content_type == "application/json":
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise MCPTransportError("MCP response exceeds the experiment's 2 MB limit.")
                result = self._server_message(json.loads(body), request_id)
                if result is not None:
                    return result["result"]
            elif content_type == "text/event-stream":
                size, parts, messages = 0, [], 0
                while time.monotonic() < deadline:
                    line = response.readline(MAX_BODY + 1)
                    size += len(line)
                    if size > MAX_BODY:
                        raise MCPTransportError("MCP stream exceeds the experiment's 2 MB limit.")
                    if not line:
                        break
                    line = line.decode("utf-8").rstrip("\r\n")
                    if line.startswith("data:"):
                        parts.append(line[5:].removeprefix(" "))
                    elif not line and parts:
                        messages += 1
                        if messages > 100:
                            raise MCPError("Too many messages before the MCP result.")
                        event = json.loads("\n".join(parts))
                        parts = []
                        if not allow_server_requests and "method" in event:
                            continue
                        result = self._server_message(event, request_id)
                        if result is not None:
                            return result["result"]
            else:
                raise MCPError("Expected a JSON or SSE response from the MCP endpoint.")
            raise MCPTransportError("MCP closed the response without a confirmed result.")
        except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            raise MCPTransportError("MCP connection failed or returned invalid data. The action may have completed; it will not be retried.") from error
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()

    def rpc(self, method, params=None):
        self.counter += 1
        message = {"jsonrpc": "2.0", "id": self.counter, "method": method}
        if params is not None:
            message["params"] = params
        return self._post(message, self.counter)

    def connect(self):
        try:
            result = self.rpc("initialize", {"protocolVersion": self.version, "capabilities": {},
                              "clientInfo": {"name": "nox-lfm3-audio-experiment", "version": "0.1.0"}})
            if not isinstance(result, dict) or result.get("protocolVersion") not in VERSIONS:
                raise MCPError("The MCP server negotiated an unsupported protocol version.")
            self.version = result["protocolVersion"]
            if "tools" not in result.get("capabilities", {}):
                raise MCPError("This MCP server does not expose tools.")
            self.name = str(result.get("serverInfo", {}).get("name", "MCP server"))[:200]
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
            cursor, seen, tools = None, set(), []
            for _ in range(32):
                page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
                if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
                    raise MCPError("The server returned an invalid tool list.")
                tools.extend(page["tools"])
                if len(tools) > 128:
                    raise MCPError("This experiment supports up to 128 tools per connection.")
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise MCPError("The server returned an invalid or repeated tool cursor.")
                seen.add(cursor)
            else:
                raise MCPError("MCP tool pagination did not finish.")
            names, aliases = set(), set()
            for index, tool in enumerate(tools):
                if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                        or not tool["name"] or len(tool["name"]) > 128 or tool["name"] in names
                        or not isinstance(tool.get("inputSchema"), dict)):
                    raise MCPError("A tool has an invalid name or input schema.")
                name = tool["name"]
                names.add(name)
                base = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:54] or "tool"
                alias, suffix = base, 0
                while alias in aliases:
                    alias = "{}_{}_{}".format(base, index, suffix)
                    suffix += 1
                aliases.add(alias)
                self.aliases[alias] = name
                self.tools.append({"name": name, "title": str(tool.get("title", name))[:200],
                                   "description": str(tool.get("description", "")),
                                   "function": {"type": "function", "name": alias,
                                                "description": str(tool.get("description", "")),
                                                "parameters": tool["inputSchema"]}})
            if len(json.dumps([tool["function"] for tool in self.tools])) > 60000:
                raise MCPError("The tool catalogue is too large for this experiment's context budget.")
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.session:
            connection = self._connection(timeout=3)
            try:
                connection.request("DELETE", self._path(), headers=self._headers())
                response = connection.getresponse()
                response.read(4096)
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
                self.session = None

    def call(self, alias, arguments):
        if alias not in self.aliases or not isinstance(arguments, dict):
            raise MCPError("Unknown tool or invalid arguments. No call was sent.")
        return self.rpc("tools/call", {"name": self.aliases[alias], "arguments": arguments})


class MCPManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.client = None
        self.connection_id = None
        self.executions = {}

    def _profile(self):
        if self.client is None:
            return {"connected": False, "tools": []}
        return {"connected": True, "id": self.connection_id, "url": self.client.url,
                "name": self.client.name, "tools": self.client.tools}

    def profile(self):
        with self.lock:
            return self._profile()

    def tool_snapshot(self, connection_id, alias):
        with self.lock:
            if self.client is None or connection_id != self.connection_id:
                raise MCPError("The MCP connection changed or was disconnected.")
            for tool in self.client.tools:
                if tool["function"]["name"] == alias:
                    return json.loads(json.dumps(tool))
            raise MCPError("Select a tool exposed by the connected MCP server.")

    def connect(self, url):
        with self.lock:
            candidate = MCPClient(url).connect()
            if self.client:
                self.client.close()
            self.client = candidate
            self.connection_id = secrets.token_hex(16)
            self.executions = {}
            return self._profile()

    def close(self):
        with self.lock:
            if self.client:
                self.client.close()
            self.client = None
            self.connection_id = None
            self.executions = {}

    def execute(self, connection_id, invocation_id, name, arguments):
        with self.lock:
            if self.client is None or connection_id != self.connection_id:
                raise MCPError("The MCP connection changed or was disconnected. Reconnect the voice conversation.")
            if not isinstance(invocation_id, str) or not 1 <= len(invocation_id) <= 256:
                raise MCPError("Invalid execution identifier.")
            if not isinstance(name, str) or name not in self.client.aliases or not isinstance(arguments, dict):
                raise MCPError("Unknown tool or invalid arguments. No call was sent.")
            signature = json.dumps([name, arguments], sort_keys=True, allow_nan=False)
            if invocation_id in self.executions:
                previous, result = self.executions[invocation_id]
                if signature != previous:
                    raise MCPError("This execution identifier was already used with different arguments.")
                return result
            if len(self.executions) >= 256:
                raise MCPError("Execution history is full. Disconnect voice and reconnect MCP to continue.")
            try:
                result = self.client.call(name, arguments)
                if not isinstance(result, dict) or ("content" not in result and "structuredContent" not in result):
                    raise MCPTransportError("The tool returned no valid result; its outcome is unknown. No automatic retry.")
                record = {"name": self.client.aliases[name], "result": result,
                          "isError": bool(result.get("isError", False)), "uncertain": False}
            except MCPError as error:
                record = {"name": self.client.aliases[name], "isError": True,
                          "uncertain": isinstance(error, MCPTransportError),
                          "result": {"isError": True, "content": [{"type": "text", "text": str(error)}]}}
            self.executions[invocation_id] = (signature, record)
            return record
