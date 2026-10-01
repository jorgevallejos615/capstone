"""High-level research workflows."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import pandas as pd

from .cache import SQLiteCache
from .client import OpenLibraryClient
from .config import Settings
from .models import Author, BenchmarkResult, Book
from .pipeline import (
    author_summary,
    authors_frame,
    books_frame,
    deduplicate_catalog,
    subject_summary,
)


class ResearchService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.cache = SQLiteCache(
            self.settings.cache_path,
            ttl_seconds=self.settings.cache_ttl_seconds,
        )
        self.client: OpenLibraryClient | None = None

    async def __aenter__(self) -> ResearchService:
        await self.cache.open()
        self.client = OpenLibraryClient(self.settings, self.cache)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.client is not None:
            await self.client.close()
        await self.cache.close()

    def _client(self) -> OpenLibraryClient:
        if self.client is None:
            raise RuntimeError("ResearchService must be used as an async context manager")
        return self.client

    async def search_books(self, query: str, limit: int = 50) -> pd.DataFrame:
        limit = max(1, min(limit, 1_000))
        page_size = min(limit, 100)
        pages = (limit + page_size - 1) // page_size
        responses = await asyncio.gather(
            *(
                self._client().search_books(query, limit=page_size, page=page)
                for page in range(1, pages + 1)
            )
        )
        docs = [doc for response in responses for doc in response.get("docs", [])][:limit]
        return books_frame(Book.from_search_doc(doc) for doc in docs)

    async def build_catalog(
        self,
        queries: Sequence[str],
        limit_each: int = 50,
    ) -> dict[str, pd.DataFrame]:
        query_list = [query.strip() for query in queries if query.strip()]
        if not query_list:
            raise ValueError("At least one non-empty query is required")
        frames = await asyncio.gather(
            *(self.search_books(query, limit_each) for query in query_list)
        )
        catalog = pd.concat(frames, ignore_index=True) if frames else books_frame([])
        unique, duplicates = deduplicate_catalog(catalog)
        return {
            "catalog": unique,
            "duplicates": duplicates,
            "authors": author_summary(unique),
            "subjects": subject_summary(unique),
        }

    async def research_subject(self, subject: str, limit: int = 50) -> dict[str, pd.DataFrame]:
        payload = await self._client().subject(subject, limit=max(1, min(limit, 1_000)))
        books = books_frame(Book.from_subject_work(work) for work in payload.get("works", []))
        return {
            "books": books,
            "authors": author_summary(books),
            "subjects": subject_summary(books),
        }

    async def research_authors(self, query: str, limit: int = 25) -> pd.DataFrame:
        payload = await self._client().search_authors(query, limit=max(1, min(limit, 100)))
        frame = authors_frame(Author.from_search_doc(doc) for doc in payload.get("docs", []))
        if frame.empty:
            return frame
        return frame.sort_values(
            ["work_count", "ratings_count"], ascending=False, ignore_index=True
        )

    async def benchmark(self, query: str, count: int = 10) -> BenchmarkResult:
        payload = await self._client().search_books(query, limit=max(1, min(count, 100)))
        keys = [str(doc["key"]) for doc in payload.get("docs", []) if doc.get("key")][:count]
        if not keys:
            raise ValueError("No works were found to benchmark")

        started = time.perf_counter()
        await self._client().fetch_works(keys, concurrent=False, use_cache=False)
        sequential = time.perf_counter() - started

        started = time.perf_counter()
        await self._client().fetch_works(keys, concurrent=True, use_cache=False)
        concurrent = time.perf_counter() - started
        return BenchmarkResult(len(keys), sequential, concurrent)
