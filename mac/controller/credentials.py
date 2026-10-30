"""Service-scoped secrets owned by the controller; reads expose status only."""

import json
import re
import threading
import time

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .config import write_private
from .hub import DownloadError, hub_json, request
from .model_files import REPOSITORY, artifact_manifest

SERVICES = {
    "huggingface": {
        "id": "huggingface", "name": "Hugging Face", "fieldLabel": "Access token",
        "help": "Read access is sufficient. PersonaPlex also requires accepting its access terms.",
        "tokenUrl": "https://huggingface.co/settings/tokens",
        "accessUrl": "https://huggingface.co/nvidia/personaplex-7b-v1",
    }
}
VALIDATORS = {"huggingface": re.compile(r"hf_[A-Za-z0-9]{8,}")}


def test_huggingface(token):
    try:
        hub_json("/api/whoami-v2", token)
    except DownloadError as error:
        return {"state": "invalid" if error.code == "MODEL_ACCESS" else "network_error",
            "message": "The token is invalid or expired." if error.code == "MODEL_ACCESS" else str(error)}
    try:
        revision = artifact_manifest()["revision"]
        with request(f"https://huggingface.co/{REPOSITORY}/resolve/{revision}/model.safetensors", token, method="HEAD"):
            pass
        return {"state": "valid", "message": "Token valid. PersonaPlex download access confirmed."}
    except DownloadError as error:
        return {"state": "access_required" if error.code == "MODEL_ACCESS" else "network_error", "message": str(error)}


TESTERS = {"huggingface": test_huggingface}


class CredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    secret: SecretStr = Field(min_length=8, max_length=512)


class Credentials:
    def __init__(self, config, definitions=lambda: {}):
        self.path = config.data_dir / "credentials.json"
        self.lock = threading.RLock()
        self.definitions = definitions

    @property
    def services(self):
        services = {**SERVICES, **self.definitions()}
        for key in self.read():
            if key not in services and re.fullmatch(r"mcp-[a-z][a-z0-9_-]{0,47}", key):
                services[key] = {"id": key, "name": key, "fieldLabel": "API key",
                    "help": "No connection currently uses this saved key. You can remove it here."}
        return services

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            if self.path.is_symlink():
                raise ValueError("Credential storage must be a private regular file.")
            return json.loads(self.path.read_text())

    def secret(self, service):
        return self.read().get(service, {}).get("secret")

    def describe(self):
        saved = self.read()
        return {"services": [{**definition, "configured": bool(saved.get(key, {}).get("secret")),
            "testSupported": key in TESTERS, "test": saved.get(key, {}).get("test"),
            "testedAt": saved.get(key, {}).get("testedAt")} for key, definition in self.services.items()]}

    def save(self, service, secret):
        if service not in self.services:
            raise ValueError("Unsupported credential service.")
        if any(ord(char) < 33 or ord(char) > 126 for char in secret):
            raise ValueError("Enter a printable bearer credential without whitespace.")
        validator = VALIDATORS.get(service)
        if validator and not validator.fullmatch(secret):
            raise ValueError("Invalid credential format for this service.")
        with self.lock:
            value = self.read()
            value[service] = {"secret": secret}
            write_private(self.path, json.dumps(value))
            return self.describe()

    def remove(self, service):
        with self.lock:
            value = self.read()
            value.pop(service, None)
            write_private(self.path, json.dumps(value))
            return self.describe()

    def test(self, service):
        token = self.secret(service)
        if not token:
            return {"state": "not_configured", "message": "Add a token for this service first."}
        tester = TESTERS.get(service)
        if tester is None:
            return {"state": "unsupported", "message": "Connection testing is not available for this service."}
        result = tester(token)
        with self.lock:
            value = self.read()
            # A slow test must not resurrect or certify a replaced/removed secret.
            if value.get(service, {}).get("secret") == token:
                value[service].update(test=result, testedAt=time.time())
                write_private(self.path, json.dumps(value))
            else:
                return {"state": "changed", "message": "The credential changed during the test. Test the current token again."}
        return result
