"""Disposable MCP test application. All notes and request traces exist only in memory."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {"name": "list_notes", "description": "List all notes in the test app, including their IDs, titles and content.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "create_note", "description": "Create a note in the test app with a title and content. Returns the created note and its ID.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["title", "content"], "additionalProperties": False}},
]


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, result=None, headers=None, sse=False):
        data = json.dumps(result).encode() if result is not None else b""
        if sse:
            data = (b": fixture heartbeat\r\n\r\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}).encode()
                    + b"\r\n\r\nevent: message\r\ndata: " + data + b"\r\n\r\n")
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):
        self.server.fixture.deleted += 1
        self.reply(204)

    def do_POST(self):
        fixture = self.server.fixture
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        fixture.requests.append((message, dict(self.headers)))
        method, params = message.get("method"), message.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": fixture.name, "version": "1"},
                      "capabilities": {"tools": {}}}
            self.reply(200, {"jsonrpc": "2.0", "id": message["id"], "result": result},
                       {"Mcp-Session-Id": "fixture-session"} if fixture.stateful else {})
            return
        if fixture.stateful and self.headers.get("Mcp-Session-Id") != "fixture-session":
            self.reply(404)
            return
        if "id" not in message:
            self.reply(202)
            return
        if method == "tools/list":
            if fixture.custom_tools is not None:
                result = {"tools": fixture.custom_tools}
            elif params.get("cursor"):
                result = {"tools": TOOLS[1:]}
            else:
                result = {"tools": TOOLS[:1], "nextCursor": "second-page"}
        elif method == "tools/call":
            fixture.calls.append(params)
            name, arguments = params.get("name"), params.get("arguments", {})
            if fixture.drop_after_call:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if name == "list_notes":
                result = {"content": [{"type": "text", "text": json.dumps(fixture.notes)}],
                          "structuredContent": {"notes": fixture.notes.copy()}}
            elif (name == "create_note" and set(arguments) == {"title", "content"}
                    and all(isinstance(value, str) for value in arguments.values())):
                note = {"id": str(len(fixture.notes) + 1), **arguments}
                fixture.notes.append(note)
                result = {"content": [{"type": "text", "text": json.dumps(note)}], "structuredContent": note}
            else:
                result = {"isError": True, "content": [{"type": "text", "text": "Invalid tool arguments."}]}
        else:
            self.reply(200, {"jsonrpc": "2.0", "id": message["id"],
                             "error": {"code": -32601, "message": "Method not found."}})
            return
        self.reply(200, {"jsonrpc": "2.0", "id": message["id"], "result": result}, sse=fixture.sse)


class MCPFixture:
    def __init__(self, sse=False, stateful=True, drop_after_call=False, custom_tools=None, name="Isolated MCP test app"):
        self.sse, self.stateful, self.drop_after_call = sse, stateful, drop_after_call
        self.custom_tools = custom_tools
        self.name = name
        self.requests, self.calls, self.notes = [], [], []
        self.deleted = 0

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server.daemon_threads = True
        self.server.fixture = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}/mcp".format(self.server.server_port)
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
