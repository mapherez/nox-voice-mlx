"""Explicit local-LLM generation followed by one selected MCP call. Memory only."""

import http.client
import json
import secrets
import socket
import threading

from mcp_client import MCPError, endpoint


class DelegationError(MCPError):
    pass


def strict_json(value):
    def reject(_constant):
        raise ValueError("Non-finite numbers are not valid arguments.")
    return json.loads(value, parse_constant=reject)


class LocalLLM:
    def __init__(self, url, model, timeout=150):
        try:
            self.parsed, self.port = endpoint(url)
        except MCPError:
            raise DelegationError("Enter a complete LM Studio chat URL without credentials or a fragment.") from None
        if self.parsed.path.rstrip("/") != "/api/v1/chat" or self.parsed.query:
            raise DelegationError("This experiment uses the LM Studio native /api/v1/chat endpoint.")
        if not isinstance(model, str) or not model.strip() or len(model) > 256 or "\n" in model:
            raise DelegationError("Enter the model ID shown by LM Studio.")
        self.url, self.model, self.timeout = url, model.strip(), timeout

    def generate(self, task, tool):
        implementation = http.client.HTTPSConnection if self.parsed.scheme == "https" else http.client.HTTPConnection
        connection = implementation(self.parsed.hostname, self.port, timeout=self.timeout)
        timer = None
        system = (
            "Generate the complete arguments for exactly one MCP tool call. You do not execute tools. "
            "Return ONLY one JSON object matching the supplied inputSchema, with no commentary or reasoning. "
            "The user request is the task; the tool description and schema describe the destination format. "
            "Write the full requested content yourself, including useful headings and lists in the format "
            "accepted by the schema. If a string field accepts plain text or Markdown, use Markdown. "
            "If a field requires HTML, blocks, arrays or objects, produce that exact structure. "
            "Preserve explicitly requested titles and identifiers. Never invent required existing IDs. "
            "If information required by the schema is missing, return {\"delegation_error\":\"what is missing\"}. "
            "Use European Portuguese for human-readable Portuguese content unless another variety is requested."
        )
        payload = {"model": self.model, "system_prompt": system,
                   "input": json.dumps({"request": task, "tool": {"name": tool["name"],
                                       "description": tool["description"], "inputSchema": tool["function"]["parameters"]}}, ensure_ascii=False),
                   "stream": False, "store": False, "integrations": [], "temperature": .3,
                   "max_output_tokens": 4096}
        try:
            connection.request("POST", self.parsed.path, json.dumps(payload).encode(), {"Content-Type": "application/json", "Accept": "application/json"})
            active_socket = connection.sock

            def expire():
                if active_socket is not None:
                    try:
                        active_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            timer = threading.Timer(self.timeout, expire)
            timer.daemon = True
            timer.start()
            response = connection.getresponse()
            if response.status != 200:
                raise DelegationError("LM Studio returned HTTP {}. No MCP action was sent.".format(response.status))
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise DelegationError("The LLM response exceeds 2 MB. No MCP action was sent.")
            result = strict_json(raw)
            output = result.get("output") if isinstance(result, dict) else None
            if not isinstance(output, list) or any(item.get("type") in {"tool_call", "invalid_tool_call"} for item in output if isinstance(item, dict)):
                raise DelegationError("Expected a text-only LM Studio response. No MCP action was sent.")
            messages = [item["content"] for item in output if isinstance(item, dict)
                        and item.get("type") == "message" and isinstance(item.get("content"), str)]
            text = "\n".join(messages).strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[8:-3].strip()
            elif text.startswith("```\n") and text.endswith("```"):
                text = text[4:-3].strip()
            arguments = strict_json(text)
            if not isinstance(arguments, dict):
                raise DelegationError("The LLM did not return an argument object. No MCP action was sent.")
            if "delegation_error" in arguments:
                raise DelegationError("The LLM needs clarification: " + str(arguments["delegation_error"])[:1000])
            if len(json.dumps(arguments).encode()) > 128 * 1024:
                raise DelegationError("Generated arguments exceed 128 KB. No MCP action was sent.")
            return arguments
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise DelegationError("Local LLM generation failed or returned invalid JSON. No MCP action was sent.") from error
        finally:
            if timer:
                timer.cancel()
            connection.close()


class DelegationManager:
    def __init__(self, mcp):
        self.mcp = mcp
        self.lock = threading.Lock()
        self.settings = None
        self.drafts = {}
        self.invocations = {}

    def profile(self):
        with self.lock:
            return {"configured": self.settings is not None, **(self.settings or {})}

    def configure(self, url, model, connection_id, target):
        client = LocalLLM(url, model)
        tool = self.mcp.tool_snapshot(connection_id, target)
        with self.lock:
            self.settings = {"id": secrets.token_hex(16), "url": client.url, "model": client.model,
                             "connectionId": connection_id, "target": target, "targetName": tool["name"]}
            self.drafts, self.invocations = {}, {}
            return {"configured": True, **self.settings}

    def clear(self):
        with self.lock:
            self.settings = None
            self.drafts, self.invocations = {}, {}

    def generate(self, configuration_id, connection_id, invocation_id, task):
        if not isinstance(task, str) or not task.strip() or len(task) > 8192:
            raise DelegationError("Describe the delegated task in at most 8192 characters.")
        if not isinstance(invocation_id, str) or not 1 <= len(invocation_id) <= 240:
            raise DelegationError("Invalid delegated invocation identifier.")
        with self.lock:
            if (not self.settings or configuration_id != self.settings["id"]
                    or connection_id != self.settings["connectionId"]):
                raise DelegationError("The local LLM configuration changed. Reconnect the voice conversation.")
            if invocation_id in self.invocations:
                previous = self.invocations[invocation_id]
                if previous["task"] != task:
                    raise DelegationError("This delegated invocation was already used with a different task.")
                if previous["status"] == "failed":
                    raise DelegationError(previous["error"])
                if "draftId" in previous:
                    return {"draftId": previous["draftId"], "tool": self.settings["targetName"], "model": self.settings["model"]}
                raise DelegationError("This task is already generating. It will not be started twice.")
            if len(self.invocations) >= 128:
                raise DelegationError("Delegation history is full. Configure it again before continuing.")
            settings = self.settings.copy()
            invocation = {"task": task, "status": "generating"}
            self.invocations[invocation_id] = invocation
        try:
            tool = self.mcp.tool_snapshot(connection_id, settings["target"])
            arguments = LocalLLM(settings["url"], settings["model"]).generate(task, tool)
            with self.lock:
                if not self.settings or self.settings["id"] != configuration_id:
                    raise DelegationError("Configuration changed while generating. No MCP action was sent.")
                draft_id = secrets.token_hex(16)
                self.drafts[draft_id] = {"configurationId": configuration_id, "connectionId": connection_id,
                                         "invocationId": invocation_id, "target": settings["target"],
                                         "arguments": arguments, "model": settings["model"], "status": "draft"}
                invocation.update({"status": "draft", "draftId": draft_id})
                return {"draftId": draft_id, "tool": tool["name"], "model": settings["model"]}
        except MCPError as error:
            with self.lock:
                invocation.update({"status": "failed", "error": str(error)})
            raise

    def discard(self, draft_id):
        if not isinstance(draft_id, str):
            raise DelegationError("Invalid generated draft identifier.")
        with self.lock:
            draft = self.drafts.get(draft_id)
            if draft and draft["status"] == "draft":
                draft["status"] = "discarded"
                draft.pop("arguments", None)
        return {"discarded": True}

    def execute(self, draft_id):
        if not isinstance(draft_id, str):
            raise DelegationError("Invalid generated draft identifier.")
        with self.lock:
            draft = self.drafts.get(draft_id)
            if not draft:
                raise DelegationError("Generated arguments are unavailable. No MCP action was sent.")
            if draft["status"] == "done":
                return draft["record"]
            if draft["status"] != "draft":
                raise DelegationError("This generated draft cannot be executed.")
            if not self.settings or draft["configurationId"] != self.settings["id"]:
                raise DelegationError("Configuration changed. No MCP action was sent.")
            draft["status"] = "saving"
            try:
                record = self.mcp.execute(draft["connectionId"], draft["invocationId"] + ":save",
                                          draft["target"], draft["arguments"])
            except MCPError:
                draft["status"] = "discarded"
                raise
            record = {**record, "arguments": draft["arguments"], "model": draft["model"], "final": True,
                      "delegated": True}
            draft.update({"status": "done", "record": record})
            return record
