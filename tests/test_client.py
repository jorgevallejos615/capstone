from pathlib import Path

import httpx
import pytest

from openlibrary_automation.cache import SQLiteCache
from openlibrary_automation.client import OpenLibraryClient
from openlibrary_automation.config import Settings


@pytest.mark.asyncio
async def test_search_uses_cache(tmp_path: Path) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.params["q"] == "python"
        return httpx.Response(
            200,
            json={"numFound": 1, "docs": [{"key": "/works/OL1W", "title": "Python"}]},
        )

    settings = Settings(cache_path=tmp_path / "cache.sqlite3")
    async with (
        SQLiteCache(settings.cache_path) as cache,
        OpenLibraryClient(
            settings,
            cache,
            transport=httpx.MockTransport(handler),
        ) as client,
    ):
        first = await client.search_books("python", limit=1)
        second = await client.search_books("python", limit=1)

    assert first == second
    assert requests == 1


@pytest.mark.asyncio
async def test_work_normalizes_key(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/works/OL123W.json"
        return httpx.Response(200, json={"key": "/works/OL123W"})

    settings = Settings(cache_path=tmp_path / "cache.sqlite3")
    async with (
        SQLiteCache(settings.cache_path) as cache,
        OpenLibraryClient(
            settings,
            cache,
            transport=httpx.MockTransport(handler),
        ) as client,
    ):
        result = await client.work("OL123W")

    assert result["key"] == "/works/OL123W"
