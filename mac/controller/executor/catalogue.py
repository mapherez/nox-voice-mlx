"""Session identities and schema-checked, namespaced MCP tools."""

import copy
import hashlib
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .protocol import ExecutionError, MCPClient, encode


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validator(schema):
    if not isinstance(schema, dict) or schema.get("$schema", "https://json-schema.org/draft/2020-12/schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ExecutionError("Only JSON Schema 2020-12 tool schemas are supported.")

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#")):
                    raise ExecutionError("External schema references are unsupported; no schema URL is fetched.")
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=Registry())
    except SchemaError:
        raise ExecutionError("MCP advertised an invalid tool schema.") from None


class Connection:
    def __init__(self, settings, secret=None, *, client=None):
        self.settings = settings.model_copy(deep=True)
        self.credential_identity = digest(secret)
        self.client = client or MCPClient(settings, secret)
        self.identity = ""
        self.tools = {}
        self.valid = False

    async def connect(self):
        if self.settings.role != "direct":
            raise ExecutionError("Suite discovery/descriptor negotiation is reserved for stage 6. Use a direct MCP profile.")
        tools = await self.client.connect()
        names = set()
        for raw in tools:
            if (not isinstance(raw, dict) or not isinstance(raw.get("name"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", raw["name"]) or raw["name"] in names):
                raise ExecutionError("MCP advertised an invalid or duplicate tool name.")
            names.add(raw["name"])
            if (not isinstance(raw.get("title", ""), str) or not isinstance(raw.get("description", ""), str)
                    or not isinstance(raw.get("_meta", {}), dict) or not isinstance(raw.get("execution", {}), dict)):
                raise ExecutionError("MCP advertised invalid tool metadata.")
            schema = raw.get("inputSchema")
            input_validator = validator(schema)
            output_validator = validator(raw["outputSchema"]) if "outputSchema" in raw else None
            metadata = raw.get("_meta", {})
            # Never interpret unknown NoX descriptors as conventional unrestricted tools.
            blocked = bool(metadata) or raw.get("execution", {}).get("taskSupport") == "required"
            alias = "mcp_" + digest([self.settings.id, raw["name"]])[:24]
            self.tools[alias] = {"raw": copy.deepcopy(raw), "input": input_validator,
                "output": output_validator, "blocked": blocked}
        self.identity = digest([self.settings.model_dump(), self.client.identity, self.client.capabilities, tools])
        self.valid = True
        return self

    def describe(self):
        return {"id": self.settings.id, "name": self.settings.name, "connected": self.valid,
            "identity": self.identity, "server": self.client.identity,
            "tools": [{"alias": alias, "name": tool["raw"]["name"],
                "title": tool["raw"].get("title", tool["raw"]["name"]),
                "supported": not tool["blocked"]} for alias, tool in self.tools.items()]}

    def definitions(self):
        definitions = []
        for alias, tool in self.tools.items():
            if tool["blocked"]:
                continue
            raw = tool["raw"]
            # Bindings replace omitted arguments with actual structured values in code.
            definitions.append({"type": "function", "function": {"name": alias,
                "description": f"{self.settings.name}: {raw.get('description', raw['name'])}\n"
                    "Input schema (validated after bindings): " + encode(raw["inputSchema"]),
                "parameters": {"type": "object", "properties": {
                    "arguments": {"type": "object", "description": "Literal tool arguments, omit bound fields."},
                    "bindings": {"type": "array", "items": {"type": "object", "properties": {
                        "target": {"type": "string", "description": "JSON Pointer into arguments, or empty for all arguments."},
                        "reference": {"type": "string", "description": "Exact reference ID supplied with a prior result."},
                        "pointer": {"type": "string", "description": "JSON Pointer into that structured result."}},
                        "required": ["target", "reference", "pointer"], "additionalProperties": False}}},
                    "required": ["arguments"], "additionalProperties": False}}})
        return definitions
