"""Opaque, revocable server-side sessions. Only token digests are stored."""

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

COOKIE = "__Host-nox_voice"
SESSION_SECONDS = 12 * 60 * 60


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "sessions.sqlite3"
        self.path.touch(mode=0o600, exist_ok=True)
        with self.connection() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL)"
            )

    @contextmanager
    def connection(self):
        database = sqlite3.connect(self.path, timeout=5)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    def create(self, email: str, subject: str) -> str:
        token = secrets.token_urlsafe(48)
        with self.connection() as database:
            database.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            database.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (
                    token_digest(token),
                    email,
                    subject,
                    secrets.token_urlsafe(32),
                    time.time() + SESSION_SECONDS,
                ),
            )
        return token

    def get(self, token: str | None) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.connection() as database:
            value = database.execute(
                "SELECT * FROM sessions WHERE token = ? AND expires > ?",
                (token_digest(token), time.time()),
            ).fetchone()
        return dict(value) if value else None

    def delete(self, token: str | None) -> None:
        if token:
            with self.connection() as database:
                database.execute("DELETE FROM sessions WHERE token = ?", (token_digest(token),))
