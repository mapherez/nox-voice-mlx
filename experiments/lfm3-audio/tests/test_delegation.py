import json
import threading
import unittest

from delegation import DelegationError, DelegationManager, LocalLLM
from llm_fixture import CONTENT, LLMFixture
from mcp_client import MCPError, MCPManager
from mcp_fixture import MCPFixture


class NativeLLM(unittest.TestCase):
    def test_native_payload_disables_storage_and_integrations_and_ignores_reasoning(self):
        with LLMFixture() as llm, MCPFixture() as fixture:
            mcp = MCPManager()
            try:
                profile = mcp.connect(fixture.url)
                tool = mcp.tool_snapshot(profile["id"], "create_note")
                arguments = LocalLLM(llm.url, "fixture-model").generate("Create a formatted note", tool)
                self.assertEqual(arguments["content"], CONTENT)
                request = llm.requests[0]
                self.assertFalse(request["store"])
                self.assertFalse(request["stream"])
                self.assertEqual(request["integrations"], [])
                self.assertEqual(json.loads(request["input"])["tool"]["inputSchema"], tool["function"]["parameters"])
                self.assertEqual(fixture.calls, [])
            finally:
                mcp.close()

    def test_invalid_output_never_becomes_an_mcp_action(self):
        outputs = [[{"type": "message", "content": "A recipe without JSON"}],
                   [{"type": "message", "content": "[]"}],
                   [{"type": "tool_call", "tool": "create_note", "arguments": {}}],
                   [{"type": "message", "content": '{"delegation_error":"Missing a destination"}'}]]
        for output in outputs:
            with self.subTest(output=output), LLMFixture(output=output) as llm, MCPFixture() as fixture:
                mcp = MCPManager()
                try:
                    profile = mcp.connect(fixture.url)
                    delegation = DelegationManager(mcp)
                    settings = delegation.configure(llm.url, "fixture", profile["id"], "create_note")
                    with self.assertRaises(DelegationError):
                        delegation.generate(settings["id"], profile["id"], "invalid", "Create a note")
                    self.assertEqual(fixture.calls, [])
                    with self.assertRaises(DelegationError):
                        delegation.generate(settings["id"], profile["id"], "invalid", "Create a note")
                    self.assertEqual(len(llm.requests), 1, "Failed generation is not silently retried")
                finally:
                    mcp.close()

    def test_only_native_chat_urls_are_accepted(self):
        for url, model in [("file:///tmp/model", "model"), ("http://localhost:1234", "model"),
                           ("http://localhost:1234/api/v1/chat", "")]:
            with self.assertRaises(DelegationError):
                LocalLLM(url, model)

    def test_generation_timeout_does_not_publish_late_output(self):
        with LLMFixture(blocked=True) as llm:
            with self.assertRaises(DelegationError):
                LocalLLM(llm.url, "fixture", timeout=.05).generate("Create a note", {
                    "name": "create_note", "description": "Create note", "function": {"parameters": {"type": "object"}}})
            self.assertEqual(len(llm.requests), 1)
            llm.release.set()


class DelegatedExecution(unittest.TestCase):
    def test_generation_is_separate_from_saving_and_content_is_preserved_exactly_once(self):
        with LLMFixture() as llm, MCPFixture() as fixture:
            mcp = MCPManager()
            try:
                profile = mcp.connect(fixture.url)
                delegation = DelegationManager(mcp)
                settings = delegation.configure(llm.url, "fixture", profile["id"], "create_note")
                draft = delegation.generate(settings["id"], profile["id"], "voice:d1", "Create a formatted note")
                self.assertEqual(fixture.notes, [])
                self.assertEqual(draft, delegation.generate(settings["id"], profile["id"], "voice:d1", "Create a formatted note"))
                self.assertEqual(len(llm.requests), 1)
                result = delegation.execute(draft["draftId"])
                self.assertEqual(fixture.notes[0]["content"], CONTENT)
                self.assertEqual(result["arguments"], fixture.calls[0]["arguments"])
                self.assertTrue(result["final"])
                self.assertTrue(result["delegated"])
                self.assertEqual(result, delegation.execute(draft["draftId"]))
                self.assertEqual(len(fixture.calls), 1)
            finally:
                mcp.close()

    def test_discarded_draft_cannot_write_and_stale_mcp_connection_cannot_write(self):
        with LLMFixture() as llm, MCPFixture() as fixture:
            mcp = MCPManager()
            try:
                profile = mcp.connect(fixture.url)
                delegation = DelegationManager(mcp)
                settings = delegation.configure(llm.url, "fixture", profile["id"], "create_note")
                draft = delegation.generate(settings["id"], profile["id"], "discard", "Create a note")
                delegation.discard(draft["draftId"])
                with self.assertRaises(DelegationError):
                    delegation.execute(draft["draftId"])
                second = delegation.generate(settings["id"], profile["id"], "stale", "Create a note")
                mcp.connect(fixture.url)
                with self.assertRaises(MCPError):
                    delegation.execute(second["draftId"])
                self.assertEqual(fixture.calls, [])
            finally:
                mcp.close()

    def test_configuration_change_during_generation_prevents_a_stale_save(self):
        with LLMFixture(blocked=True) as llm, MCPFixture() as fixture:
            mcp = MCPManager()
            try:
                profile = mcp.connect(fixture.url)
                delegation = DelegationManager(mcp)
                settings = delegation.configure(llm.url, "fixture", profile["id"], "create_note")
                errors = []

                def generate():
                    try:
                        delegation.generate(settings["id"], profile["id"], "changing", "Create a note")
                    except MCPError as error:
                        errors.append(str(error))

                thread = threading.Thread(target=generate)
                thread.start()
                self.assertTrue(llm.started.wait(3))
                delegation.clear()
                llm.release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertTrue(errors)
                self.assertEqual(fixture.calls, [])
            finally:
                mcp.close()

    def test_lost_mcp_result_stays_unknown_without_another_generation_or_save(self):
        with LLMFixture() as llm, MCPFixture(drop_after_call=True) as fixture:
            mcp = MCPManager()
            try:
                profile = mcp.connect(fixture.url)
                delegation = DelegationManager(mcp)
                settings = delegation.configure(llm.url, "fixture", profile["id"], "create_note")
                draft = delegation.generate(settings["id"], profile["id"], "uncertain", "Create a note")
                result = delegation.execute(draft["draftId"])
                self.assertTrue(result["uncertain"])
                self.assertEqual(delegation.execute(draft["draftId"]), result)
                self.assertEqual(len(fixture.calls), 1)
                self.assertEqual(len(llm.requests), 1)
            finally:
                mcp.close()
