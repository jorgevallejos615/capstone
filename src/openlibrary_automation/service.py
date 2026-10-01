"""High-level research workflows."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from .cache import SQLiteCache
from .catalog_io import deduplicate_stream, stream_catalog
from .client import OpenLibraryClient, OpenLibraryError
from .config import Settings
from .models import Author, BenchmarkResult, Book
from .pipeline import (
    author_summary,
    authors_frame,
    books_frame,
    deduplicate_catalog,
    subject_summary,
)
from .reports import write_report


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
        book_models = [Book.from_subject_work(work) for work in payload.get("works", [])]
        await self.cache.upsert_books(book_models, source=f"subject:{subject}")
        books = books_frame(book_models)
        return {
            "books": books,
            "authors": author_summary(books),
            "subjects": subject_summary(books),
        }

    async def ingest_subject_comparison(
        self,
        subject: str,
        page_depth: int,
        page_size: int = 50,
    ) -> tuple[dict[str, pd.DataFrame], BenchmarkResult]:
        """Fetch subject pages sequentially with sync HTTP and concurrently with async HTTP."""
        slug = subject.strip().casefold().replace(" ", "_")
        offsets = [page * page_size for page in range(page_depth)]

        def sync_fetch() -> list[dict[str, object]]:
            responses: list[dict[str, object]] = []
            with httpx.Client(
                base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds,
                headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
                follow_redirects=True,
            ) as client:
                for offset in offsets:
                    response = client.get(
                        f"/subjects/{slug}.json",
                        params={"limit": page_size, "offset": offset, "details": "true"},
                    )
                    response.raise_for_status()
                    responses.append(response.json())
            return responses

        started = time.perf_counter()
        try:
            sync_responses = await asyncio.to_thread(sync_fetch)
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenLibraryError(f"Synchronous subject fetch failed: {exc}") from exc
        sync_seconds = time.perf_counter() - started

        started = time.perf_counter()
        async_responses = await asyncio.gather(
            *(
                self._client().get_json(
                    f"/subjects/{slug}.json",
                    {"limit": page_size, "offset": offset, "details": "true"},
                    use_cache=False,
                )
                for offset in offsets
            )
        )
        async_seconds = time.perf_counter() - started
        payloads = [payload for payload in async_responses if isinstance(payload, dict)]
        if not payloads:
            payloads = sync_responses
        works = [work for payload in payloads for work in payload.get("works", [])]
        models = [Book.from_subject_work(work) for work in works]
        await self.cache.upsert_books(models, source=f"subject:{subject}")
        books = books_frame(models)
        return (
            {
                "books": books,
                "authors": author_summary(books),
                "subjects": subject_summary(books),
            },
            BenchmarkResult(len(models), sync_seconds, async_seconds),
        )

    async def search_local_then_api(self, query: str, limit: int = 25) -> tuple[pd.DataFrame, str]:
        local = await self.cache.search_catalog(query, limit)
        if local:
            return pd.DataFrame(local), "local database"
        payload = await self._client().search_books(query, limit=limit)
        models = [Book.from_search_doc(doc) for doc in payload.get("docs", [])]
        await self.cache.upsert_books(models, source=f"search:{query}")
        return books_frame(models), "Open Library API"

    async def ingest_catalog_file(self, path: Path) -> int:
        if not path.is_file():
            raise ValueError(f"Catalog file does not exist: {path}")
        books = deduplicate_stream(stream_catalog(path))
        return await self.cache.upsert_books(books, source=f"file:{path.name}")

    async def generate_library_reports(self, output_dir: Path) -> list[Path]:
        frame = pd.DataFrame(await self.cache.all_catalog())
        if frame.empty:
            raise ValueError("The local catalog is empty; ingest or search for books first")
        for column in ("first_publish_year", "edition_count", "ratings_count", "ratings_average"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        unique, duplicates = deduplicate_catalog(frame)
        datasets = {
            "Catalog": unique,
            "Key Metrics": pd.DataFrame(
                [
                    {
                        "total_books": len(unique),
                        "duplicate_rows": len(duplicates),
                        "unique_authors": unique["authors"].nunique(),
                        "average_editions": unique["edition_count"].mean(),
                        "average_rating": unique["ratings_average"].mean(),
                    }
                ]
            ),
            "Top Authors": author_summary(unique),
            "Top Subjects": subject_summary(unique),
        }
        stamp = datetime.now().strftime("%Y%m%d")
        output_dir.mkdir(parents=True, exist_ok=True)
        excel = write_report(datasets, output_dir / f"library_report_{stamp}.xlsx")

        years = (
            unique.dropna(subset=["first_publish_year"])
            .groupby("first_publish_year", as_index=False)
            .size()
            .rename(columns={"size": "book_count"})
            .sort_values("book_count", ascending=False)
            .head(25)
        )
        languages = self._language_summary(unique)
        csv_paths = write_report(
            {"publication_years": years, "languages": languages},
            output_dir / "library_summary.csv",
        )
        json_path = output_dir / "library_catalog.min.json"
        json_path.write_text(unique.to_json(orient="records"), encoding="utf-8")
        return [*excel, *csv_paths, json_path]

    @staticmethod
    def _language_summary(frame: pd.DataFrame) -> pd.DataFrame:
        exploded = frame.assign(
            language=frame["languages"].fillna("").astype(str).str.split(r";\s*")
        ).explode("language")
        exploded["language"] = exploded["language"].astype(str).str.strip()
        return (
            exploded[exploded["language"].ne("")]
            .groupby("language", as_index=False)
            .size()
            .rename(columns={"size": "book_count"})
            .sort_values("book_count", ascending=False)
        )

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
