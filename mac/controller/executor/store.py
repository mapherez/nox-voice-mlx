"""Bounded in-memory task receipts and identity-scoped structured references."""

import copy
import secrets
import time

from .protocol import ExecutionError, encode

TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled", "outcome_unknown"}


def pointer(value, path):
    if path == "":
        return copy.deepcopy(value)
    if not isinstance(path, str) or not path.startswith("/"):
        raise ExecutionError("References require an explicit JSON Pointer.")
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) and key.isdigit() else value[key]
        return copy.deepcopy(value)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ExecutionError("The referenced value is missing. Refresh the source result.") from None


def bind(arguments, bindings, references, identities):
    value = copy.deepcopy(arguments)
    if not isinstance(value, dict) or not isinstance(bindings, list):
        raise ExecutionError("Tool arguments and bindings must be objects and a list.")
    used = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"target", "reference", "pointer"}:
            raise ExecutionError("Invalid structured-result binding.")
        reference = references.get(binding["reference"])
        if reference is None or not reference.get("valid", True) or identities.get(reference["connection"]) != reference["identity"]:
            raise ExecutionError("The reference is stale or belongs to another connection/session.")
        replacement = pointer(reference["value"], binding["pointer"])
        target = binding["target"]
        if not isinstance(target, str) or target in used:
            raise ExecutionError("Bindings must have distinct JSON Pointer targets.")
        if any(not target or not other or target.startswith(other + "/") or other.startswith(target + "/") for other in used):
            raise ExecutionError("Overlapping reference bindings are ambiguous.")
        used.add(target)
        if target == "":
            value = replacement
        else:
            if not target.startswith("/"):
                raise ExecutionError("Invalid argument binding target.")
            parts = target[1:].split("/")
            cursor = value
            try:
                for part in parts[:-1]:
                    key = part.replace("~1", "/").replace("~0", "~")
                    cursor = cursor[int(key)] if isinstance(cursor, list) else cursor[key]
                key = parts[-1].replace("~1", "/").replace("~0", "~")
                if isinstance(cursor, list):
                    cursor[int(key)] = replacement
                else:
                    cursor[key] = replacement
            except (KeyError, IndexError, TypeError, ValueError):
                raise ExecutionError("The binding target does not exist in the arguments.") from None
    if not isinstance(value, dict):
        raise ExecutionError("Resolved tool arguments must be an object.")
    return value


class TaskStore:
    def __init__(self, owner, session_id, limits):
        self.owner, self.session_id, self.limits = owner, session_id, limits
        self.tasks, self.references = {}, {}
        self.closed = False

    def size(self):
        return len(encode([self.tasks, self.references]).encode())

    def admit(self, turn_id, transcript, revision, identities, endpoint_identity):
        if self.closed:
            raise ExecutionError("This conversation has ended.")
        prior = next((task for task in self.tasks.values() if task["turnId"] == turn_id), None)
        if prior:
            if prior["transcript"] != transcript:
                raise ExecutionError("A turn ID cannot be reused with a different transcript.")
            return prior
        pending = sum(task["state"] not in TERMINAL for task in self.tasks.values())
        if pending >= self.limits.maxPending:
            raise ExecutionError("The task queue is full. Wait for a task to finish.")
        if len(self.tasks) >= self.limits.maxTasks or self.size() >= self.limits.memoryBytes:
            raise ExecutionError("Conversation activity memory is full. End this conversation before starting another.")
        task = {"taskId": secrets.token_hex(16), "sessionId": self.session_id, "turnId": turn_id,
            "transcript": transcript, "settingsRevision": revision, "catalogues": identities.copy(),
            "endpointIdentity": endpoint_identity, "state": "queued", "steps": [], "references": [],
            "output": "", "speech": "", "error": "", "presentation": "pending", "createdAt": time.time()}
        if self.size() + len(encode(task).encode()) + 1024 > self.limits.memoryBytes:
            raise ExecutionError("Conversation activity memory is full. No task was admitted.")
        self.tasks[task["taskId"]] = task
        return task

    def reference(self, task, step, connection, value):
        key = secrets.token_hex(16)
        self.references[key] = {"connection": connection.settings.id, "identity": connection.identity,
            "taskId": task["taskId"], "invocationId": step["invocationId"], "value": copy.deepcopy(value)}
        task["references"].append(key)
        return key

    def snapshot(self):
        return {"sessionId": self.session_id, "closed": self.closed, "tasks": copy.deepcopy(list(self.tasks.values()))}
