"""Asynchronous SQLite response cache."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

from .models import Book


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
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog (
                work_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT NOT NULL DEFAULT '',
                author_keys TEXT NOT NULL DEFAULT '',
                first_publish_year INTEGER,
                edition_count INTEGER NOT NULL DEFAULT 0,
                isbn TEXT NOT NULL DEFAULT '',
                subjects TEXT NOT NULL DEFAULT '',
                languages TEXT NOT NULL DEFAULT '',
                publisher TEXT,
                ratings_average REAL,
                ratings_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_title ON catalog(title)")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_authors ON catalog(authors)")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
            """
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
            await self._increment_metric("cache_misses")
            return None
        self.hits += 1
        await self._increment_metric("cache_hits")
        return json.loads(row[0])

    async def _increment_metric(self, name: str) -> None:
        db = self._connection()
        await db.execute(
            """
            INSERT INTO metrics(name, value) VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET value = value + 1
            """,
            (name,),
        )
        await db.commit()

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

    async def upsert_books(self, books: Iterable[Book], source: str) -> int:
        """Stream books into the searchable catalog in bounded batches."""
        statement = """
            INSERT INTO catalog(
                work_key, title, authors, author_keys, first_publish_year,
                edition_count, isbn, subjects, languages, publisher,
                ratings_average, ratings_count, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_key) DO UPDATE SET
                title = excluded.title,
                authors = excluded.authors,
                author_keys = excluded.author_keys,
                first_publish_year = excluded.first_publish_year,
                edition_count = MAX(catalog.edition_count, excluded.edition_count),
                isbn = excluded.isbn,
                subjects = excluded.subjects,
                languages = excluded.languages,
                publisher = excluded.publisher,
                ratings_average = excluded.ratings_average,
                ratings_count = excluded.ratings_count,
                source = excluded.source,
                updated_at = excluded.updated_at
            """
        batch: list[tuple[Any, ...]] = []
        total = 0
        now = time.time()
        for book in books:
            record = book.as_record()
            work_key = book.key.strip() or self._fallback_key(record)
            batch.append(
                (
                    work_key,
                    record["title"],
                    record["authors"],
                    record["author_keys"],
                    record["first_publish_year"],
                    record["edition_count"],
                    record["isbn"],
                    record["subjects"],
                    record["languages"],
                    record["publisher"],
                    record["ratings_average"],
                    record["ratings_count"],
                    source,
                    now,
                )
            )
            if len(batch) >= 500:
                await self._connection().executemany(statement, batch)
                await self._connection().commit()
                total += len(batch)
                batch.clear()
        if batch:
            await self._connection().executemany(statement, batch)
            await self._connection().commit()
            total += len(batch)
        return total

    @staticmethod
    def _fallback_key(record: dict[str, Any]) -> str:
        identity = f"{record['title']}|{record['authors']}|{record['isbn']}".casefold()
        return f"local:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    async def search_catalog(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        pattern = f"%{query.casefold()}%"
        cursor = await self._connection().execute(
            """
            SELECT work_key AS key, title, authors, author_keys, first_publish_year,
                   edition_count, isbn, subjects, languages, publisher,
                   ratings_average, ratings_count
            FROM catalog
            WHERE lower(title) LIKE ? OR lower(authors) LIKE ? OR lower(isbn) LIKE ?
            ORDER BY ratings_count DESC, edition_count DESC, title
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        )
        columns = [description[0] for description in cursor.description or []]
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    async def all_catalog(self) -> list[dict[str, Any]]:
        cursor = await self._connection().execute(
            """
            SELECT work_key AS key, title, authors, author_keys, first_publish_year,
                   edition_count, isbn, subjects, languages, publisher,
                   ratings_average, ratings_count
            FROM catalog ORDER BY title
            """
        )
        columns = [description[0] for description in cursor.description or []]
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    async def database_metrics(self) -> dict[str, int | float]:
        db = self._connection()
        counts: dict[str, int | float] = {}
        for table in ("catalog", "api_cache"):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            row = await cursor.fetchone()
            await cursor.close()
            counts[f"{table}_rows"] = int(row[0] if row else 0)
        cursor = await db.execute("SELECT name, value FROM metrics")
        counts.update({str(name): int(value) for name, value in await cursor.fetchall()})
        await cursor.close()
        hits = int(counts.get("cache_hits", 0))
        misses = int(counts.get("cache_misses", 0))
        counts["cache_hit_ratio"] = hits / (hits + misses) if hits + misses else 0.0
        storage_files = (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        )
        counts["database_bytes"] = sum(
            path.stat().st_size for path in storage_files if path.exists()
        )
        return counts
