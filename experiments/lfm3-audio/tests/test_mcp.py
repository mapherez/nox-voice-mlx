import http.client
import json
import threading
import unittest

import experiment
from mcp_client import MCPClient, MCPError, MCPManager, endpoint
from mcp_fixture import TOOLS, MCPFixture


class MCPTransport(unittest.TestCase):
    def test_json_and_sse_negotiate_stateful_and_stateless_sessions_with_pagination(self):
        for sse, stateful in [(False, True), (True, True), (True, False)]:
            with self.subTest(sse=sse, stateful=stateful), MCPFixture(sse=sse, stateful=stateful) as fixture:
                client = MCPClient(fixture.url).connect()
                try:
                    self.assertEqual(client.version, "2025-06-18")
                    self.assertEqual([tool["name"] for tool in client.tools], ["list_notes", "create_note"])
                    initialized, headers = fixture.requests[1]
                    self.assertEqual(initialized["method"], "notifications/initialized")
                    self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
                    result = client.call("create_note", {"title": "Test", "content": "Only in fixture memory"})
                    self.assertEqual(result["structuredContent"]["id"], "1")
                    self.assertEqual(len(fixture.notes), 1)
                finally:
                    client.close()
                self.assertEqual(fixture.deleted, int(stateful))

    def test_no_non_http_urls_credentials_fragments_or_invalid_ports(self):
        for url in ["file:///etc/passwd", "http://user:pass@localhost/mcp", "http://localhost/mcp#fragment",
                    "http://localhost:invalid/mcp", "http://local host/mcp", None]:
            with self.subTest(url=url), self.assertRaises(MCPError):
                endpoint(url)

    def test_name_aliases_remain_unique_and_map_back_to_original_names(self):
        names = ["a_b_2_0", "a.b", "a_b"]
        tools = [{**TOOLS[0], "name": name} for name in names]
        with MCPFixture(custom_tools=tools) as fixture:
            client = MCPClient(fixture.url).connect()
            try:
                aliases = [tool["function"]["name"] for tool in client.tools]
                self.assertEqual(len(set(aliases)), 3)
                for alias, name in zip(aliases, names):
                    self.assertEqual(client.aliases[alias], name)
            finally:
                client.close()

    def test_unknown_tools_and_non_object_arguments_never_reach_server(self):
        with MCPFixture() as fixture:
            client = MCPClient(fixture.url).connect()
            try:
                for name, args in [("not_exposed", {}), ("list_notes", [])]:
                    with self.assertRaises(MCPError):
                        client.call(name, args)
                self.assertEqual(fixture.calls, [])
            finally:
                client.close()


class MCPExecutions(unittest.TestCase):
    def test_confirmed_write_is_not_repeated_for_the_same_execution_id(self):
        with MCPFixture() as fixture:
            manager = MCPManager()
            try:
                profile = manager.connect(fixture.url)
                first = manager.execute(profile["id"], "voice:call1", "create_note", {"title": "Test", "content": "hello"})
                again = manager.execute(profile["id"], "voice:call1", "create_note", {"title": "Test", "content": "hello"})
                self.assertEqual(first, again)
                self.assertFalse(first["isError"])
                self.assertEqual(len(fixture.calls), 1)
                with self.assertRaises(MCPError):
                    manager.execute(profile["id"], "voice:call1", "create_note", {"title": "Different", "content": "hello"})
            finally:
                manager.close()

    def test_lost_result_is_marked_unknown_and_never_replayed(self):
        with MCPFixture(drop_after_call=True) as fixture:
            manager = MCPManager()
            try:
                profile = manager.connect(fixture.url)
                first = manager.execute(profile["id"], "uncertain", "create_note", {"title": "Test", "content": "hello"})
                self.assertTrue(first["isError"])
                self.assertTrue(first["uncertain"])
                self.assertEqual(first, manager.execute(profile["id"], "uncertain", "create_note", {"title": "Test", "content": "hello"}))
                self.assertEqual(len(fixture.calls), 1)
            finally:
                manager.close()

    def test_old_connection_cannot_execute_against_a_replacement_server(self):
        with MCPFixture() as fixture:
            manager = MCPManager()
            try:
                old = manager.connect(fixture.url)
                current = manager.connect(fixture.url)
                self.assertNotEqual(old["id"], current["id"])
                with self.assertRaises(MCPError):
                    manager.execute(old["id"], "old-call", "list_notes", {})
                self.assertEqual(fixture.calls, [])
            finally:
                manager.close()

    def test_tool_validation_error_remains_a_confirmed_failure(self):
        with MCPFixture() as fixture:
            manager = MCPManager()
            try:
                profile = manager.connect(fixture.url)
                result = manager.execute(profile["id"], "bad-args", "create_note", {"title": "Missing content"})
                self.assertTrue(result["isError"])
                self.assertFalse(result["uncertain"])
                self.assertEqual(fixture.notes, [])
            finally:
                manager.close()


class MCPHttp(unittest.TestCase):
    def test_browser_api_connects_executes_and_disconnects_with_same_origin_only(self):
        with MCPFixture(sse=True) as fixture:
            server = experiment.LocalServer(("127.0.0.1", 0), 1, "test-only")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port

            def post(path, body, origin=None):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    headers = {"Content-Type": "application/json"}
                    if origin is not None:
                        headers["Origin"] = origin
                    connection.request("POST", path, json.dumps(body), headers)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                self.assertEqual(post("/api/mcp/connect", {"url": fixture.url})[0], 403)
                self.assertEqual(post("/api/mcp/connect", {"url": fixture.url}, "https://foreign.test")[0], 403)
                self.assertEqual(fixture.requests, [])
                origin = "http://127.0.0.1:" + str(port)
                status, profile = post("/api/mcp/connect", {"url": fixture.url}, origin)
                self.assertEqual(status, 200)
                self.assertTrue(profile["connected"])
                status, result = post("/api/mcp/call", {"connectionId": profile["id"], "invocationId": "http-call",
                                                       "name": "list_notes", "arguments": {}}, origin)
                self.assertEqual(status, 200)
                self.assertFalse(result["isError"])
                self.assertEqual(post("/api/mcp/disconnect", {}, origin)[0], 200)
                self.assertFalse(server.mcp.profile()["connected"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
