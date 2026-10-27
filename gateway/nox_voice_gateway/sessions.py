"""Opaque, revocable server-side sessions. Only token digests are stored."""

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

COOKIE = "__Host-nox_voice"
SESSION_SECONDS = 12 * 60 * 60


def cookie_name(secure: bool) -> str:
    # __Host- cookies require Secure, so local HTTP uses a separate cookie name.
    return COOKIE if secure else "nox_voice_local"


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
            columns = {row[1] for row in database.execute("PRAGMA table_info(sessions)")}
            if "origin" not in columns:
                database.execute("ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connection(self):
        database = sqlite3.connect(self.path, timeout=5)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    def create(self, email: str, subject: str, origin: str) -> str:
        token = secrets.token_urlsafe(48)
        with self.connection() as database:
            database.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            database.execute(
                "INSERT INTO sessions (token, email, subject, csrf, expires, origin) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token_digest(token),
                    email,
                    subject,
                    secrets.token_urlsafe(32),
                    time.time() + SESSION_SECONDS,
                    origin,
                ),
            )
        return token

    def get(self, token: str | None, origin: str | None = None) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.connection() as database:
            value = database.execute(
                "SELECT * FROM sessions WHERE token = ? AND expires > ?",
                (token_digest(token), time.time()),
            ).fetchone()
        if value and (origin is None or value["origin"] == origin):
            return dict(value)
        return None

    def delete(self, token: str | None) -> None:
        if token:
            with self.connection() as database:
                database.execute("DELETE FROM sessions WHERE token = ?", (token_digest(token),))
