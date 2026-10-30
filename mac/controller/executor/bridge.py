"""Correlate worker audio and controller task events on one ordered session wire."""

import asyncio
import secrets

from fastapi import HTTPException, WebSocketDisconnect

from .protocol import ExecutionError


def summary(task):
    return {key: value for key, value in task.items() if key not in {"steps", "transcript", "output"}} | {
        "transcript": task["transcript"][:1000], "output": task["output"][:4000],
        "stepCount": len(task["steps"])}


class TaskBridge:
    def __init__(self, manager, socket, revision):
        self.manager, self.socket, self.revision = manager, socket, revision
        self.owner = socket.headers.get("x-nox-session-owner") or secrets.token_hex(32)
        self.sequence, self.turn = 0, 0
        self.store = None
        self.lock = asyncio.Lock()
        self.closed = False

    async def emit(self, value):
        async with self.lock:
            if self.closed:
                return
            self.sequence += 1
            try:
                await self.socket.send_json({**value, "sequence": self.sequence})
            except (RuntimeError, OSError, WebSocketDisconnect):
                self.closed = True
                if self.store:
                    self.store.closed = True

    async def publish(self, task):
        await self.emit({"type": "task.updated", "sessionId": task["sessionId"], "task": summary(task)})

    async def worker(self, value):
        if value.get("type") == "session.ready":
            self.store = self.manager.executor.open_session(value["sessionId"], self.owner)
            value["settingsRevision"] = self.revision
            value["capabilities"]["executor"] = self.manager.executor.settings.value.enabled
        if value.get("type") == "turn.started":
            self.turn = value["turnId"]
        await self.emit(value)
        if value.get("type") == "transcript.completed" and self.store and value.get("turnId") == self.turn:
            try:
                task = self.manager.executor.admit(self.store, self.turn, value.get("text", ""), self.revision, self.publish)
                if task:
                    await self.publish(task)
            except ExecutionError as error:
                await self.emit({"type": "error", "sessionId": self.store.session_id,
                    "turnId": self.turn, "code": "EXECUTOR_UNAVAILABLE", "message": str(error)})

    async def control(self, value):
        kind = value.get("type", "")
        if not isinstance(kind, str) or not kind.startswith("task."):
            return False
        if not self.store or value.get("sessionId") != self.store.session_id:
            raise ValueError("Task control belongs to another session.")
        if kind == "task.list" and set(value) == {"type", "sessionId"}:
            for task in self.store.tasks.values():
                await self.publish(task)
        elif kind == "task.cancel" and set(value) == {"type", "sessionId", "taskId"} and isinstance(value["taskId"], str):
            try:
                task = await self.manager.executor.cancel(self.store, value["taskId"])
                await self.publish(task)
            except HTTPException:
                raise ValueError("Unknown task.") from None
        else:
            raise ValueError("Unsupported task control.")
        return True

    async def close(self):
        self.closed = True
        if self.store:
            await self.manager.executor.end_session(self.store)
