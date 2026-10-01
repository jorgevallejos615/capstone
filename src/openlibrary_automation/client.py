"""Resilient asynchronous client for the public Open Library API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import Iterable
from typing import Any

import httpx

from .cache import SQLiteCache
from .config import Settings


class OpenLibraryError(RuntimeError):
    """Raised when an Open Library request cannot be completed."""


class OpenLibraryClient:
    def __init__(
        self,
        settings: Settings,
        cache: SQLiteCache,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers={
                "Accept": "application/json",
                "User-Agent": settings.user_agent,
            },
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> OpenLibraryClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any] | None) -> str:
        serialized = json.dumps(
            {"path": path, "params": params or {}},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any] | list[Any]:
        cache_key = self._cache_key(path, params)
        if use_cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return cached

        response: httpx.Response | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.get(path, params=params)
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise OpenLibraryError(f"Invalid JSON returned by {path}") from exc
                if not isinstance(payload, (dict, list)):
                    raise OpenLibraryError(f"Unexpected response type from {path}")
                if use_cache:
                    await self.cache.set(cache_key, payload)
                return payload
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(
                    exc, httpx.HTTPStatusError
                ) or exc.response.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt >= self.settings.max_retries:
                    status = response.status_code if response is not None else "network"
                    raise OpenLibraryError(f"Request to {path} failed ({status}): {exc}") from exc
                retry_after = response.headers.get("Retry-After") if response is not None else None
                delay = (
                    min(float(retry_after), 30.0)
                    if retry_after and retry_after.replace(".", "", 1).isdigit()
                    else min(0.5 * (2**attempt) + random.uniform(0, 0.25), 8.0)
                )
                await asyncio.sleep(delay)

        raise OpenLibraryError(f"Request to {path} failed")

    async def search_books(
        self,
        query: str,
        *,
        limit: int = 25,
        page: int = 1,
        fields: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "limit": limit, "page": page}
        if fields:
            params["fields"] = fields
        payload = await self.get_json("/search.json", params)
        if not isinstance(payload, dict):
            raise OpenLibraryError("Book search returned an invalid payload")
        return payload

    async def search_authors(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        payload = await self.get_json("/search/authors.json", {"q": query, "limit": limit})
        if not isinstance(payload, dict):
            raise OpenLibraryError("Author search returned an invalid payload")
        return payload

    async def subject(self, subject: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        slug = subject.strip().lower().replace(" ", "_")
        payload = await self.get_json(
            f"/subjects/{slug}.json",
            {"limit": limit, "offset": offset, "details": "true"},
        )
        if not isinstance(payload, dict):
            raise OpenLibraryError("Subject lookup returned an invalid payload")
        return payload

    async def work(self, key: str, *, use_cache: bool = True) -> dict[str, Any]:
        normalized = key if key.startswith("/works/") else f"/works/{key}"
        payload = await self.get_json(f"{normalized}.json", use_cache=use_cache)
        if not isinstance(payload, dict):
            raise OpenLibraryError("Work lookup returned an invalid payload")
        return payload

    async def author(self, key: str, *, use_cache: bool = True) -> dict[str, Any]:
        normalized = key if key.startswith("/authors/") else f"/authors/{key}"
        payload = await self.get_json(f"{normalized}.json", use_cache=use_cache)
        if not isinstance(payload, dict):
            raise OpenLibraryError("Author lookup returned an invalid payload")
        return payload

    async def fetch_works(
        self,
        keys: Iterable[str],
        *,
        concurrent: bool = True,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(keys))
        if concurrent:
            return list(
                await asyncio.gather(*(self.work(key, use_cache=use_cache) for key in normalized))
            )
        results = []
        for key in normalized:
            results.append(await self.work(key, use_cache=use_cache))
        return results
