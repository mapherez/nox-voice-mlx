"""Explicit connection lifecycle and conversation-owned task admission."""

import asyncio
import copy
from contextlib import suppress

from fastapi import HTTPException

from .catalogue import Connection, digest
from .config import SettingsStore
from .llm import LLMClient
from .protocol import ExecutionError
from .runner import Runner
from .store import TERMINAL, TaskStore


class ExecutorService:
    def __init__(self, manager):
        self.manager = manager
        self.settings = SettingsStore(manager.config)
        self.connections = {}
        self.stores = {}
        self.jobs = {}
        self.queue = asyncio.Lock()
        self.connection_lock = asyncio.Lock()

    @property
    def credentials(self):
        return self.manager.library.credentials

    def secret(self, reference):
        value = self.credentials.secret(reference) if reference else None
        if reference and not value:
            raise ExecutionError("Add the selected credential in Credentials before connecting.")
        return value

    def sanitize(self, value, extra=()):
        # A malicious endpoint must not echo configured secrets into browser output/model input.
        secrets_ = [*extra, *(entry.get("secret") for entry in self.credentials.read().values() if entry.get("secret"))]

        def redact(item):
            if isinstance(item, str):
                for secret in secrets_:
                    item = item.replace(secret, "[redacted]")
                return item
            if isinstance(item, dict):
                return {redact(key): redact(child) for key, child in item.items()}
            if isinstance(item, list):
                return [redact(child) for child in item]
            return item

        return redact(value)

    async def save(self, proposed):
        if self.manager.active or any(not store.closed for store in self.stores.values()):
            raise HTTPException(409, "End the conversation before changing executor settings.")
        async with self.connection_lock:
            previous = self.settings.value
            saved = self.settings.save(proposed)
            if previous != saved:
                await self.disconnect_all()
            return saved.model_dump()

    def catalogue(self):
        return {"connections": [self.sanitize(connection.describe()) for connection in self.connections.values()]}

    def current(self, connection):
        return self.connections.get(connection.settings.id) is connection and connection.credential_identity == digest(
            self.credentials.secret(connection.settings.credential) if connection.settings.credential else None)

    async def test_llm(self):
        settings = self.settings.value.llm.model_copy(deep=True)
        client = LLMClient(settings, self.secret(settings.credential))
        try:
            return self.sanitize(await client.test())
        finally:
            await client.close()

    async def connection_action(self, connection_id, action):
        async with self.connection_lock:
            if self.manager.active:
                raise HTTPException(409, "End the conversation before changing MCP connections.")
            settings = next((item for item in self.settings.value.connections if item.id == connection_id), None)
            if settings is None:
                raise HTTPException(404, "Unknown configured MCP connection.")
            if action == "disconnect":
                previous = self.connections.pop(connection_id, None)
                if previous:
                    previous.valid = False
                    await previous.client.close()
                return self.catalogue()
            candidate = Connection(settings, self.secret(settings.credential))
            try:
                async with asyncio.timeout(settings.timeoutSeconds):
                    await candidate.connect()
                result = self.sanitize(candidate.describe())
                if action == "test":
                    return result
                previous = self.connections.get(connection_id)
                self.connections[connection_id] = candidate
                if previous:
                    previous.valid = False
                    await previous.client.close()
                return self.catalogue()
            except TimeoutError:
                raise ExecutionError("MCP connection/discovery deadline reached. No business tool was called.") from None
            finally:
                if action == "test" or self.connections.get(connection_id) is not candidate:
                    await candidate.client.close()

    def open_session(self, session_id, owner):
        # One live audio conversation, bounded terminal activity from the previous one.
        self.stores = {key: store for key, store in self.stores.items() if not store.closed}
        store = TaskStore(owner, session_id, self.settings.value.limits.model_copy(deep=True))
        self.stores[session_id] = store
        return store

    def store(self, session_id, owner):
        store = self.stores.get(session_id)
        if store is None or store.owner != owner:
            raise HTTPException(404, "Conversation activity is unavailable for this session.")
        return store

    def admit(self, store, turn_id, transcript, voice_revision, publish):
        settings = self.settings.value.model_copy(deep=True)
        if not settings.enabled:
            return None
        if not transcript.strip():
            return None
        if len(transcript) > 16000:
            raise ExecutionError("Transcript exceeds the task input limit.")
        if not settings.llm.model.strip():
            raise ExecutionError("Choose an executor model in Settings.")
        connections = {}
        for profile in settings.connections:
            connection = self.connections.get(profile.id)
            if not connection or not connection.valid or connection.client.changed or not self.current(connection):
                raise ExecutionError("Connect or refresh every configured MCP endpoint before starting a task.")
            connections[profile.id] = connection
        secret = self.secret(settings.llm.credential)
        secret_snapshot = tuple(entry["secret"] for entry in self.credentials.read().values() if entry.get("secret"))
        prior = next((task for task in store.tasks.values() if task["turnId"] == turn_id), None)
        if prior:
            if prior["transcript"] != transcript:
                raise ExecutionError("A turn ID cannot be reused with a different transcript.")
            return prior
        identities = {key: connection.identity for key, connection in connections.items()}
        task = store.admit(turn_id, transcript, settings.revision, identities,
            digest([settings.llm.model_dump(), digest(secret)]))
        task["voiceSettingsRevision"] = voice_revision
        if task["taskId"] in self.jobs or task["state"] in TERMINAL:
            return task

        async def run():
            llm = LLMClient(settings.llm, secret)
            runner = Runner(store, task, settings, connections, llm, publish, self.current)
            runner.sanitize = lambda value: self.sanitize(value, secret_snapshot)
            try:
                async with self.queue:
                    await runner.run()
            finally:
                await llm.close()

        job = asyncio.create_task(run())
        self.jobs[task["taskId"]] = job

        def finished(completed):
            self.jobs.pop(task["taskId"], None)
            if not completed.cancelled():
                completed.exception()  # Never log a traceback containing endpoint/model context.

        job.add_done_callback(finished)
        return task

    async def cancel(self, store, task_id):
        task = store.tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "Unknown task in this conversation.")
        if task["state"] not in TERMINAL:
            task["state"] = "cancel_requested"
            job = self.jobs.get(task_id)
            if job:
                job.cancel()
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(asyncio.gather(job, return_exceptions=True)), timeout=3)
            if task["state"] == "cancel_requested":
                task["state"] = "cancelled"
        return copy.deepcopy(task)

    async def end_session(self, store):
        store.closed = True  # Dispatch guard closes before waiting on any remote cancellation.
        await asyncio.gather(*(self.cancel(store, key) for key in store.tasks), return_exceptions=True)

    async def end_all(self):
        await asyncio.gather(*(self.end_session(store) for store in self.stores.values()), return_exceptions=True)

    async def disconnect_all(self):
        connections, self.connections = self.connections, {}
        for connection in connections.values():
            connection.valid = False
        await asyncio.gather(*(item.client.close() for item in connections.values()), return_exceptions=True)

    async def close(self):
        await self.end_all()
        await self.disconnect_all()
