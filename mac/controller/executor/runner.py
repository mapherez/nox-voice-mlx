"""Sequential dependent tool decisions with actual receipts and no round ceiling."""

import asyncio
import json

from .catalogue import digest
from .llm import SYSTEM
from .protocol import ExecutionError, OutcomeUnknown, RejectedCall, encode
from .store import bind, pointer

REFERENCE_TOOL = {"type": "function", "function": {"name": "nox_read_reference",
    "description": "Read a structured result retained in this conversation. Use a JSON Pointer for a subset if large.",
    "parameters": {"type": "object", "properties": {"reference": {"type": "string"},
        "pointer": {"type": "string"}}, "required": ["reference", "pointer"], "additionalProperties": False}}}


class Runner:
    def __init__(self, store, task, settings, connections, llm, publish, current):
        self.store, self.task, self.settings = store, task, settings
        self.connections, self.llm, self.publish, self.current = connections, llm, publish, current
        self.identities = task["catalogues"]
        self.tools = {alias: (connection, tool) for connection in connections.values()
            for alias, tool in connection.tools.items()}
        self.inflight = None
        self.sanitize = lambda value: value

    async def update(self, **values):
        self.task.update(values)
        await self.notify()

    async def notify(self):
        try:
            await self.publish(self.task)
        except Exception:
            self.task["presentation"] = "unavailable"
            self.store.closed = True

    def guard(self):
        if self.store.closed or self.task["state"] == "cancel_requested":
            raise asyncio.CancelledError
        for key, connection in self.connections.items():
            if not connection.valid or connection.client.changed or not self.current(connection):
                raise ExecutionError("MCP connection/catalogue changed. Refresh the connection before another task.")
            if self.identities.get(key) != connection.identity:
                raise ExecutionError("Task catalogue identity is stale. No call was sent.")

    async def invoke(self, invocation_id, alias, payload):
        signature = digest([alias, payload])
        previous = next((step for step in self.task["steps"] if step["invocationId"] == invocation_id), None)
        if previous:
            if previous["signature"] != signature:
                raise ExecutionError("Invocation ID was reused with different arguments.")
            return {"receipt": previous["receipt"], "reference": previous.get("reference"), "connection": previous["connection"]}
        self.guard()
        if alias not in self.tools:
            raise ExecutionError("The executor selected a tool outside its catalogue. No call was sent.")
        connection, tool = self.tools[alias]
        if tool["blocked"]:
            raise ExecutionError("This tool requires unsupported descriptor or asynchronous protocol features. No call was sent.")
        if not isinstance(payload, dict) or set(payload) - {"arguments", "bindings"} or "arguments" not in payload:
            raise ExecutionError("Invalid tool envelope. No call was sent.")
        arguments = bind(payload["arguments"], payload.get("bindings", []), self.store.references, self.identities)
        try:
            tool["input"].validate(arguments)
        except Exception as error:
            # Schema exceptions can embed secret arguments; never forward their text.
            raise ExecutionError("Tool arguments do not match its supported schema. No call was sent.") from error
        step = {"invocationId": invocation_id, "signature": signature, "connection": connection.settings.id,
            "catalogueIdentity": connection.identity, "tool": tool["raw"]["name"],
            "arguments": arguments, "state": "prepared", "receipt": None}
        if self.store.size() + len(encode(step).encode()) + 2048 > self.settings.limits.memoryBytes:
            raise ExecutionError("Task receipt memory budget reached. No call was sent.")
        self.task["steps"].append(step)
        await self.update(state="executing")
        self.guard()
        self.inflight = connection
        try:
            step["state"] = "dispatching"
            result = await connection.client.call(tool["raw"]["name"], arguments, invocation_id)
            result = self.sanitize(result)
            if not isinstance(result, dict) or not isinstance(result.get("isError", False), bool) or not (
                    isinstance(result.get("content"), list) or isinstance(result.get("structuredContent"), dict)):
                raise OutcomeUnknown("Tool returned no valid receipt; outcome is unknown. No automatic retry.")
            if self.store.size() + len(encode(result).encode()) * 2 + 2048 > self.settings.limits.memoryBytes:
                # Keep confirmed success/error distinct even if the full result cannot be retained.
                step.update(state="failed" if result.get("isError") else "succeeded",
                    receipt={"confirmed": True, "isError": result.get("isError", False), "retentionError": "Result exceeds remaining activity memory."})
                raise ExecutionError("The tool returned a confirmed result too large for remaining activity memory. No action is replayed.")
            step.update(state="failed" if result.get("isError") else "succeeded", receipt=result)
            if result.get("isError"):
                # A generic error cannot prove which old IDs remain current. Require fresh results.
                for reference in self.store.references.values():
                    if reference["connection"] == connection.settings.id:
                        reference["valid"] = False
            if tool["output"] is not None and not result.get("isError"):
                try:
                    tool["output"].validate(result.get("structuredContent"))
                except Exception:
                    step["state"] = "outcome_unknown"
                    raise OutcomeUnknown("Tool receipt violates its advertised output schema. No automatic retry.") from None
            reference = None
            if isinstance(result.get("structuredContent"), dict) and not result.get("isError"):
                reference = self.store.reference(self.task, step, connection, result["structuredContent"])
            step["reference"] = reference
            await self.notify()
            return {"receipt": result, "reference": reference, "connection": connection.settings.id}
        except asyncio.CancelledError:
            step.update(state="outcome_unknown", receipt={"confirmed": False, "error": "Cancelled after dispatch; remote effect may have completed."})
            await connection.client.cancel()
            raise
        except OutcomeUnknown as error:
            step.update(state="outcome_unknown", receipt={"confirmed": False, "error": str(error)})
            raise
        except RejectedCall as error:
            step.update(state="failed", receipt={"confirmed": True, "isError": True, "error": str(error)})
            raise
        finally:
            self.inflight = None

    def context(self):
        return [{"reference": key, "connection": ref["connection"], "taskId": ref["taskId"],
            "request": self.store.tasks[ref["taskId"]]["transcript"],
            "source": next(({"tool": step["tool"], "arguments": step["arguments"]}
                for step in self.store.tasks[ref["taskId"]]["steps"] if step["invocationId"] == ref["invocationId"]), None)}
            for key, ref in self.store.references.items() if ref.get("valid", True) and self.identities.get(ref["connection"]) == ref["identity"]]

    async def run(self):
        tools = [definition for connection in self.connections.values() for definition in connection.definitions()]
        if tools:
            tools.append(REFERENCE_TOOL)
        messages = [{"role": "system", "content": SYSTEM + "\n" + self.settings.llm.instructions},
            {"role": "user", "content": encode({"originalTranscript": self.task["transcript"],
                "priorStructuredResults": self.context(),
                "conversationContext": [{"transcript": prior["transcript"], "status": prior["state"],
                    "answerExcerpt": prior["output"][:1000]} for prior in self.store.tasks.values()
                    if prior["taskId"] != self.task["taskId"]],
                "unsupportedTools": [tool["raw"]["name"] for _, tool in self.tools.values() if tool["blocked"]]})}]
        try:
            async with asyncio.timeout(self.settings.limits.taskTimeoutSeconds):
                while True:
                    self.guard()
                    await self.update(state="planning")
                    self.guard()
                    # On pressure compact only tool presentation, never objective or stored references.
                    if len(encode([messages, tools]).encode()) > self.settings.limits.contextBytes * 0.8:
                        for message in messages:
                            if message["role"] == "tool" and len(message["content"]) > 512:
                                message["content"] = encode({"retainedReferences": self.context(),
                                    "note": "Full structured receipts remain available through nox_read_reference; no call is repeated."})
                    answer = self.sanitize(await self.llm.complete(self.sanitize(messages), self.sanitize(tools), self.settings.limits.contextBytes))
                    self.guard()
                    calls = answer.get("tool_calls")
                    if not calls:
                        await self.finish(answer.get("content"))
                        return
                    if not isinstance(calls, list) or len(calls) != 1:
                        raise ExecutionError("Executor must issue one dependent tool decision at a time. No batch was dispatched.")
                    call = calls[0]
                    if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("id"), str) or not 1 <= len(call["id"]) <= 256:
                        raise ExecutionError("Executor returned an invalid tool call.")
                    function = call.get("function", {})
                    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                        raise ExecutionError("Executor returned an invalid tool selection.")
                    try:
                        payload = json.loads(function["arguments"])
                    except (KeyError, ValueError, TypeError):
                        raise ExecutionError("Executor tool arguments are not valid JSON.") from None
                    messages.append(answer)
                    if function["name"] == "nox_read_reference":
                        if not isinstance(payload, dict) or set(payload) != {"reference", "pointer"}:
                            raise ExecutionError("Invalid reference lookup.")
                        reference = self.store.references.get(payload["reference"])
                        if not reference or not reference.get("valid", True) or self.identities.get(reference["connection"]) != reference["identity"]:
                            raise ExecutionError("The requested reference is stale or unavailable.")
                        result = {"reference": payload["reference"], "value": pointer(reference["value"], payload["pointer"])}
                        if len(encode(result).encode()) > self.settings.limits.contextBytes // 3:
                            result = {"error": "Use a more specific JSON Pointer; this value exceeds the context budget."}
                    else:
                        invocation = self.task["taskId"] + ":" + call["id"]
                        result = await self.invoke(invocation, function["name"], payload)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": encode(result)})
        except asyncio.CancelledError:
            for step in self.task["steps"]:
                if step["state"] == "prepared":
                    step["state"] = "cancelled"
            unknown = any(step["state"] == "outcome_unknown" for step in self.task["steps"])
            await self.update(state="outcome_unknown" if unknown else "cancelled", error="Conversation or task ended. Dispatched effects are not rolled back.")
        except OutcomeUnknown as error:
            await self.update(state="outcome_unknown", error=str(error))
        except (ExecutionError, TimeoutError) as error:
            succeeded = any(step["state"] == "succeeded" for step in self.task["steps"])
            unknown = any(step["state"] == "outcome_unknown" for step in self.task["steps"])
            await self.update(state="outcome_unknown" if unknown else "partially_succeeded" if succeeded else "failed",
                error=str(error) if isinstance(error, ExecutionError) else "Task deadline reached; no further steps will be dispatched.")
        except Exception:
            # Unexpected internal failures after dispatch cannot establish a rollback.
            unresolved = any(step["state"] in {"dispatching", "outcome_unknown"} for step in self.task["steps"])
            for step in self.task["steps"]:
                if step["state"] == "dispatching":
                    step.update(state="outcome_unknown", receipt={"confirmed": False, "error": "No valid receipt could be retained after dispatch."})
            await self.update(state="outcome_unknown" if unresolved else "failed", error="Executor failed. No operation will be replayed.")

    async def finish(self, content):
        try:
            value = json.loads(content)
            if not isinstance(value, dict) or set(value) != {"status", "output", "speech"} or value["status"] not in {
                    "succeeded", "partially_succeeded", "failed", "awaiting_input"} or not isinstance(value["output"], str) or not isinstance(value["speech"], str):
                raise ValueError()
            if not value["output"].strip() or len(value["output"]) > 32000 or not 1 <= len(value["speech"]) <= 2000:
                raise ValueError()
        except (ValueError, TypeError):
            raise ExecutionError("Executor did not return a valid outcome and concise presentation.") from None
        failed = any(step["state"] == "failed" for step in self.task["steps"])
        succeeded = any(step["state"] == "succeeded" for step in self.task["steps"])
        if value["status"] == "succeeded" and failed:
            raise ExecutionError("Executor claimed success despite a failed receipt. Inspect the recorded results.")
        state = value["status"]
        if state == "failed" and succeeded:
            state = "partially_succeeded"
        if self.store.size() + len(encode(value).encode()) > self.settings.limits.memoryBytes:
            raise ExecutionError("Outcome exceeds the remaining activity memory budget.")
        await self.update(state=state, output=value["output"], speech=value["speech"], presentation="ready")
