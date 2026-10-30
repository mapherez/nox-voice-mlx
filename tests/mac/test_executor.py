"""Executor invariants with generic, disposable tools; never real product writes."""

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from controller.config import ControllerConfig
from controller.executor.catalogue import Connection, validator
from controller.executor.config import ExecutorSettings, MCPSettings, SettingsStore
from controller.executor.llm import LLMClient
from controller.executor.protocol import ExecutionError, MCPClient, OutcomeUnknown
from controller.executor.runner import Runner
from controller.executor.store import TaskStore, bind
from controller.server import create_app
from controller.supervisor import Supervisor
from fastapi import HTTPException
from fastapi.testclient import TestClient

TOOLS = [
    {"name": "search.anything", "description": "Search by the exact provided title.", "inputSchema": {
        "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}},
    {"name": "remove.anything", "description": "Remove the given exact ID and revision.", "inputSchema": {
        "type": "object", "properties": {"id": {"type": "string"}, "revision": {"type": "integer"}},
        "required": ["id", "revision"], "additionalProperties": False}},
]


class ToolsClient:
    identity = {"name": "Arbitrary fixture", "version": "1"}
    capabilities = {"tools": {}}
    changed = False

    def __init__(self, tools=None):
        self.tools, self.calls, self.cancelled = tools or TOOLS, [], False
        self.fail = False
        self.wait = False
        self.started = asyncio.Event()

    async def connect(self):
        return copy.deepcopy(self.tools)

    async def call(self, name, arguments, invocation_id):
        self.calls.append((name, arguments, invocation_id))
        self.started.set()
        if self.wait:
            await asyncio.Event().wait()
        if self.fail:
            raise OutcomeUnknown("Lost response; do not retry.")
        return {"content": [], "structuredContent": {"items": [{"id": "real-object", "revision": 7}]}}

    async def cancel(self):
        self.cancelled = True

    async def close(self):
        pass


class LLM:
    def __init__(self, decide):
        self.decide, self.round = decide, 0

    async def complete(self, messages, tools, context_bytes):
        self.round += 1
        return self.decide(self.round, messages, tools)


def call(name, arguments, call_id="step", bindings=None):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps({"arguments": arguments, "bindings": bindings or []})}}]}


def finish(status="succeeded", text="Done."):
    return {"role": "assistant", "content": json.dumps({"status": status, "output": text, "speech": text})}


async def setup(decide, *, tools=None):
    settings = ExecutorSettings(enabled=True, llm={"model": "fixture"})
    client = ToolsClient(tools)
    connection = await Connection(MCPSettings(id="one", name="Fixture", url="http://127.0.0.1:1/mcp"), client=client).connect()
    store = TaskStore("owner", "s" * 32, settings.limits)
    task = store.admit(1, 'Find "Hello World" and delete the matching results.', 0, {"one": connection.identity}, "llm")
    events = []

    async def publish(value):
        events.append(copy.deepcopy(value))

    runner = Runner(store, task, settings, {"one": connection}, LLM(decide), publish, lambda _: True)
    aliases = list(connection.tools)
    return runner, client, aliases, events


async def test_compound_request_uses_actual_receipt_bindings_and_preserves_original_transcript():
    def decide(round_, messages, tools):
        aliases = [item["function"]["name"] for item in tools]
        assert 'Hello World' in messages[1]["content"]
        if round_ == 1:
            return call(aliases[0], {"title": "Hello World"}, "search")
        if round_ == 2:
            result = json.loads(messages[-1]["content"])
            assert result["receipt"]["structuredContent"]["items"][0]["id"] == "real-object"
            return call(aliases[1], {}, "remove", [
                {"target": "/id", "reference": result["reference"], "pointer": "/items/0/id"},
                {"target": "/revision", "reference": result["reference"], "pointer": "/items/0/revision"}])
        return finish()

    runner, client, _, _ = await setup(decide)
    await runner.run()
    assert runner.task["state"] == "succeeded"
    assert client.calls[1][1] == {"id": "real-object", "revision": 7}
    assert len(runner.task["steps"]) == 2


async def test_ordinary_answer_needs_no_mcp_and_no_fixed_tool_round_ceiling():
    runner, client, _, _ = await setup(lambda *_: finish(text="The answer is 42."))
    runner.connections = {}
    runner.tools = {}
    await runner.run()
    assert runner.task["output"] == "The answer is 42." and client.calls == []
    runner, client, _, _ = await setup(lambda n, _, tools: call(tools[0]["function"]["name"], {"title": str(n)}, str(n)) if n <= 15 else finish())
    await runner.run()
    assert runner.task["state"] == "succeeded" and len(client.calls) == 15


async def test_invalid_schema_unknown_tool_and_batch_never_dispatch():
    for make in [lambda tools: call(tools[1]["function"]["name"], {"id": "missing revision"}),
            lambda tools: call("invented-tool", {}),
            lambda tools: {**call(tools[0]["function"]["name"], {"title": "a"}), "tool_calls": call(tools[0]["function"]["name"], {"title": "b"})["tool_calls"] * 2}]:
        runner, client, _, _ = await setup(lambda _, __, tools: make(tools))
        await runner.run()
        assert runner.task["state"] == "failed" and client.calls == []


async def test_invocation_delivery_is_deduplicated_and_argument_reuse_rejected():
    runner, client, aliases, _ = await setup(lambda *_: finish())
    payload = {"arguments": {"title": "Same"}}
    first = await runner.invoke("id", aliases[0], payload)
    assert await runner.invoke("id", aliases[0], payload) == first
    assert len(client.calls) == 1
    with pytest.raises(ExecutionError, match="different arguments"):
        await runner.invoke("id", aliases[0], {"arguments": {"title": "Different"}})

    async def presentation_failed(task):
        if task["state"] == "succeeded":
            raise RuntimeError("Fixture browser disconnected after the action completed")

    runner.publish = presentation_failed
    await runner.run()
    assert runner.task["state"] == "succeeded" and runner.task["presentation"] == "unavailable"
    assert len(client.calls) == 1


async def test_changed_identity_and_stale_reference_block_before_dispatch():
    runner, client, aliases, _ = await setup(lambda *_: finish())
    reference = await runner.invoke("first", aliases[0], {"arguments": {"title": "Original"}})
    connection = runner.connections["one"]
    connection.identity = "changed"
    with pytest.raises(ExecutionError, match="stale"):
        await runner.invoke("next", aliases[1], {"arguments": {}})
    with pytest.raises(ExecutionError, match="stale"):
        bind({}, [{"target": "/id", "reference": reference["reference"], "pointer": "/items/0/id"}], runner.store.references, {"one": "changed"})
    assert len(client.calls) == 1


async def test_unknown_write_stops_without_replay_or_false_success():
    runner, client, _, _ = await setup(lambda _, __, tools: call(tools[0]["function"]["name"], {"title": "x"}))
    client.fail = True
    await runner.run()
    assert runner.task["state"] == "outcome_unknown" and len(client.calls) == 1
    assert runner.task["steps"][0]["receipt"]["confirmed"] is False


async def test_cancellation_after_dispatch_is_unknown_and_requests_cooperative_cancellation():
    runner, client, _, _ = await setup(lambda _, __, tools: call(tools[0]["function"]["name"], {"title": "x"}))
    client.wait = True
    job = asyncio.create_task(runner.run())
    await client.started.wait()
    job.cancel()
    await job
    assert runner.task["state"] == "outcome_unknown" and client.cancelled and len(client.calls) == 1

    before, unsent, _, _ = await setup(lambda _, __, tools: call(tools[0]["function"]["name"], {"title": "x"}))

    async def end_before_dispatch(task):
        if task["state"] == "executing":
            before.store.closed = True

    before.publish = end_before_dispatch
    await before.run()
    assert before.task["state"] == "cancelled" and unsent.calls == []
    assert before.task["steps"][0]["state"] == "cancelled"


async def test_task_deadline_after_dispatch_does_not_disguise_unknown_as_failed():
    runner, client, _, _ = await setup(lambda _, __, tools: call(tools[0]["function"]["name"], {"title": "x"}))
    runner.settings.limits = runner.settings.limits.model_copy(update={"taskTimeoutSeconds": .01})
    client.wait = True
    await runner.run()
    assert runner.task["state"] == "outcome_unknown" and client.cancelled


async def test_tool_error_invalidates_old_references_and_prevents_false_success():
    runner, client, aliases, _ = await setup(lambda *_: finish())
    result = await runner.invoke("source", aliases[0], {"arguments": {"title": "old"}})

    async def failure(*_):
        return {"isError": True, "content": [{"type": "text", "text": "Object is stale"}]}

    client.call = failure
    await runner.invoke("failed", aliases[0], {"arguments": {"title": "changed"}})
    assert runner.store.references[result["reference"]]["valid"] is False
    with pytest.raises(ExecutionError, match="stale"):
        bind({}, [{"target": "/id", "reference": result["reference"], "pointer": "/items/0/id"}], runner.store.references, runner.identities)
    with pytest.raises(ExecutionError, match="failed receipt"):
        await runner.finish(finish()["content"])


async def test_required_descriptor_metadata_is_unavailable_not_silently_ignored():
    tools = copy.deepcopy(TOOLS)
    tools[0]["_meta"] = {"requiredFeature": "fixture-confirmation"}
    runner, client, aliases, _ = await setup(lambda *_: finish(), tools=tools)
    assert runner.connections["one"].describe()["tools"][0]["supported"] is False
    with pytest.raises(ExecutionError, match="unsupported descriptor"):
        await runner.invoke("no", aliases[0], {"arguments": {"title": "x"}})
    assert client.calls == []


async def test_service_turn_redelivery_never_replays_a_completed_or_awaiting_input_task(tmp_path, monkeypatch):
    import controller.executor.service as service

    config = ControllerConfig(data_dir=tmp_path)
    manager = Supervisor(config)
    manager.executor.settings.value = ExecutorSettings(enabled=True, llm={"model": "fixture"})

    class Answer(LLM):
        def __init__(self, *args):
            super().__init__(lambda *_: finish("awaiting_input", "Which set?"))

        async def close(self):
            pass

    monkeypatch.setattr(service, "LLMClient", Answer)
    store = manager.executor.open_session("s" * 32, "owner")

    async def publish(_):
        pass

    try:
        task = manager.executor.admit(store, 1, "Those results", 0, publish)
        await asyncio.gather(*manager.executor.jobs.values())
        assert task["state"] == "awaiting_input"
        assert manager.executor.admit(store, 1, "Those results", 0, publish) is task
        assert not manager.executor.jobs
        await manager.executor.end_session(store)
        assert task["state"] == "cancelled"
    finally:
        await manager.close()


async def test_partial_failure_and_ambiguous_followup_remain_explicit():
    runner, _, _, _ = await setup(lambda n, _, tools: call(tools[0]["function"]["name"], {"title": "x"}) if n == 1 else finish("failed", "The remaining step could not be completed."))
    await runner.run()
    assert runner.task["state"] == "partially_succeeded"
    runner, client, _, _ = await setup(lambda *_: finish("awaiting_input", "Which of those result sets do you mean?"))
    await runner.run()
    assert runner.task["state"] == "awaiting_input" and client.calls == []


def test_reference_binding_is_explicit_and_never_extracts_ids_from_unstructured_text():
    with pytest.raises(ExecutionError, match="stale"):
        bind({}, [{"target": "/id", "reference": "prose-id", "pointer": ""}], {}, {})
    for schema in [{"$ref": "https://example.invalid/schema"}, {"$schema": "unsupported"}]:
        with pytest.raises(ExecutionError):
            validator(schema)


def test_configuration_is_private_revisioned_and_does_not_connect_or_start_models(tmp_path):
    config = ControllerConfig(data_dir=tmp_path)
    store = SettingsStore(config)
    value = store.value.model_copy(deep=True)
    value.llm.model, value.enabled = "fixture-model", True
    assert store.save(value).revision == 1
    assert store.path.stat().st_mode & 0o077 == 0
    with pytest.raises(HTTPException) as error:
        store.save(value)
    assert error.value.status_code == 409
    manager = Supervisor(config)
    with TestClient(create_app(config, manager)) as native:
        assert native.get("/api/voice/v1/executor/settings").json()["llm"]["model"] == "fixture-model"
        unchanged = native.get("/api/voice/v1/executor/settings").json()
        assert native.put("/api/voice/v1/executor/settings", json=unchanged).json()["revision"] == 1
        assert native.get("/api/voice/v1/executor/connections").json() == {"connections": []}
        assert manager.state == "stopped" and not manager.executor.jobs
        assert native.get("/api/voice/v1/sessions/unknown/tasks").status_code == 404


def test_removing_a_profile_keeps_its_saved_key_visible_and_removable(tmp_path):
    manager = Supervisor(ControllerConfig(data_dir=tmp_path))
    with TestClient(create_app(manager.config, manager)) as client:
        settings = ExecutorSettings(connections=[{"id": "key", "name": "Named server", "url": "http://127.0.0.1:1/mcp", "credential": "mcp-key"}])
        assert client.put("/api/voice/v1/executor/settings", json=settings.model_dump()).status_code == 200
        assert client.put("/api/voice/v1/credentials/mcp-key", json={"secret": "saved-fixture-key"}).status_code == 200
        settings.revision, settings.connections = 1, []
        assert client.put("/api/voice/v1/executor/settings", json=settings.model_dump()).status_code == 200
        described = client.get("/api/voice/v1/credentials")
        assert "mcp-key" in described.text and "saved-fixture-key" not in described.text
        assert client.delete("/api/voice/v1/credentials/mcp-key").status_code == 200
        assert not manager.library.credentials.secret("mcp-key")


@pytest.mark.parametrize("sse", [False, True])
async def test_streamable_http_handshake_pagination_session_headers_and_sse(sse):
    requests = []

    def serve(request):
        body = json.loads(request.content)
        requests.append((body, request.headers))
        if "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            assert request.headers["mcp-session-id"] == "session-one"
            result = {"tools": [TOOLS[0]], "nextCursor": "next"} if not body["params"] else {"tools": [TOOLS[1]]}
        else:
            result = {"content": [], "structuredContent": {"ok": True}}
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"Mcp-Session-Id": "session-one"}
        if sse:
            return httpx.Response(200, headers={**headers, "Content-Type": "text/event-stream"}, text="id: prime\ndata:\n\ndata: " + json.dumps(message) + "\n\n")
        return httpx.Response(200, headers=headers, json=message)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        settings = MCPSettings(id="fixture", name="Fixture", url="https://example.test/mcp")
        client = MCPClient(settings, "private-bearer", client=http)
        assert len(await client.connect()) == 2
        result = await client.call("search.anything", {"title": "t"}, "invocation")
        assert result["structuredContent"] == {"ok": True}
        assert all(headers["authorization"] == "Bearer private-bearer" for _, headers in requests)
        assert [body["method"] for body, _ in requests][:2] == ["initialize", "notifications/initialized"]


async def test_context_budget_and_queue_capacity_are_resource_limits_not_tool_round_limits():
    settings = ExecutorSettings(llm={"model": "fixture"})
    store = TaskStore("o", "s", settings.limits.model_copy(update={"maxPending": 1}))
    store.admit(1, "one", 0, {}, "llm")
    with pytest.raises(ExecutionError, match="queue is full"):
        store.admit(2, "two", 0, {}, "llm")
    http = SimpleNamespace()
    client = LLMClient(settings.llm, client=http)
    with pytest.raises(ExecutionError, match="context budget"):
        await client.complete([{"role": "user", "content": "objective" * 1000}], [], 200)
