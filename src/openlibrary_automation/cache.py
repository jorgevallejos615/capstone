"""Asynchronous SQLite response cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


class SQLiteCache:
    """Persistent JSON cache with per-entry expiry and lightweight metrics."""

    def __init__(self, path: Path, ttl_seconds: int = 86_400) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._db: aiosqlite.Connection | None = None
        self.hits = 0
        self.misses = 0

    async def open(self) -> SQLiteCache:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_cache_expiry ON api_cache(expires_at)"
        )
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> SQLiteCache:
        return await self.open()

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Cache is not open")
        return self._db

    async def get(self, key: str) -> dict[str, Any] | list[Any] | None:
        db = self._connection()
        cursor = await db.execute(
            "SELECT payload FROM api_cache WHERE cache_key = ? AND expires_at > ?",
            (key, time.time()),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(row[0])

    async def set(
        self,
        key: str,
        payload: dict[str, Any] | list[Any],
        ttl_seconds: int | None = None,
    ) -> None:
        db = self._connection()
        now = time.time()
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        await db.execute(
            """
            INSERT INTO api_cache(cache_key, payload, fetched_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (key, json.dumps(payload, ensure_ascii=False), now, now + ttl),
        )
        await db.commit()

    async def clear(self) -> int:
        db = self._connection()
        cursor = await db.execute("DELETE FROM api_cache")
        await db.commit()
        return cursor.rowcount

    async def purge_expired(self) -> int:
        db = self._connection()
        cursor = await db.execute("DELETE FROM api_cache WHERE expires_at <= ?", (time.time(),))
        await db.commit()
        return cursor.rowcount

    async def count(self) -> int:
        cursor = await self._connection().execute("SELECT COUNT(*) FROM api_cache")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0] if row else 0)
