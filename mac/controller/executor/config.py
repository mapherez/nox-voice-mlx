"""Private endpoint profiles; credentials are references, never settings values."""

import json
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import write_private


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.query or any(c.isspace() for c in value)):
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ValueError("Use an HTTP(S) URL without credentials, query or fragment.") from None
    return value.rstrip("/")


class LLMSettings(Strict):
    baseUrl: str = Field(default="http://127.0.0.1:1234/v1", max_length=2048)
    model: str = Field(default="", max_length=200)
    credential: Literal["", "executor"] = ""
    instructions: str = Field(default="Be helpful and concise. Reply in the user's language.", max_length=8192)
    temperature: float = Field(default=0.3, ge=0, le=2)
    maxTokens: int = Field(default=2048, ge=64, le=32768)
    timeoutSeconds: int = Field(default=120, ge=5, le=1800)

    _url = field_validator("baseUrl")(endpoint)


class MCPSettings(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    role: Literal["direct", "suite"] = "direct"
    credential: str = Field(default="", max_length=64)
    timeoutSeconds: int = Field(default=120, ge=5, le=1800)

    _url = field_validator("url")(endpoint)

    @field_validator("credential")
    @classmethod
    def credential_reference(cls, value):
        if value and not re.fullmatch(r"mcp-[a-z][a-z0-9_-]{0,47}", value):
            raise ValueError("Choose a named MCP credential reference.")
        return value


class Limits(Strict):
    maxTasks: int = Field(default=32, ge=2, le=256)
    maxPending: int = Field(default=4, ge=1, le=32)
    memoryBytes: int = Field(default=8 * 1024 * 1024, ge=1024 * 1024, le=64 * 1024 * 1024)
    contextBytes: int = Field(default=128 * 1024, ge=8192, le=2 * 1024 * 1024)
    taskTimeoutSeconds: int = Field(default=600, ge=10, le=7200)


class ExecutorSettings(Strict):
    schemaVersion: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    enabled: bool = False
    llm: LLMSettings = Field(default_factory=LLMSettings)
    connections: list[MCPSettings] = Field(default_factory=list, max_length=16)
    limits: Limits = Field(default_factory=Limits)

    @field_validator("connections")
    @classmethod
    def unique_connections(cls, values):
        if len({value.id for value in values}) != len(values):
            raise ValueError("Connection IDs must be unique.")
        return values


class ExecutorUpdate(ExecutorSettings):
    schemaVersion: Literal[1] = Field(...)
    revision: int = Field(..., ge=0)
    enabled: bool = Field(...)
    llm: LLMSettings = Field(...)
    connections: list[MCPSettings] = Field(..., max_length=16)
    limits: Limits = Field(...)


class SettingsStore:
    def __init__(self, config):
        self.path = config.data_dir / "executor-settings.json"
        self.value = ExecutorSettings.model_validate_json(self.path.read_text()) if self.path.exists() else ExecutorSettings()

    def save(self, proposed):
        proposed = ExecutorSettings.model_validate(proposed.model_dump())
        if proposed.revision != self.value.revision:
            raise HTTPException(409, "Executor settings changed. Reload before saving.")
        if proposed.enabled and not proposed.llm.model.strip():
            raise HTTPException(422, "Choose an executor model before enabling conversation.")
        if proposed == self.value:
            return self.value
        proposed = proposed.model_copy(deep=True)
        proposed.revision += 1
        try:
            write_private(self.path, json.dumps(proposed.model_dump(), indent=2) + "\n")
        except OSError:
            raise HTTPException(503, "Could not save executor settings on the controller.") from None
        self.value = proposed
        return proposed

    def credential_definitions(self):
        definitions = {"executor": {"id": "executor", "name": "Executor LLM", "fieldLabel": "API key",
            "help": "Optional bearer credential for the configured LLM endpoint."}}
        for connection in self.value.connections:
            if connection.credential:
                definitions[connection.credential] = {"id": connection.credential,
                    "name": connection.name, "fieldLabel": "API key",
                    "help": "Bearer credential for this MCP connection. Test it in Executor & MCP."}
        return definitions
