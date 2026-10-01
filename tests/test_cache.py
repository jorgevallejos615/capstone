from pathlib import Path

import pytest

from openlibrary_automation.cache import SQLiteCache


@pytest.mark.asyncio
async def test_cache_round_trip_and_clear(tmp_path: Path) -> None:
    async with SQLiteCache(tmp_path / "cache.sqlite3", ttl_seconds=60) as cache:
        assert await cache.get("missing") is None
        await cache.set("book", {"title": "Test"})

        assert await cache.get("book") == {"title": "Test"}
        assert cache.hits == 1
        assert cache.misses == 1
        assert await cache.count() == 1
        assert await cache.clear() == 1


@pytest.mark.asyncio
async def test_expired_entry_is_a_miss(tmp_path: Path) -> None:
    async with SQLiteCache(tmp_path / "cache.sqlite3", ttl_seconds=0) as cache:
        await cache.set("expired", {"value": 1})

        assert await cache.get("expired") is None
        assert await cache.purge_expired() == 1
