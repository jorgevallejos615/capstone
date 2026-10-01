"""Streaming local catalog ingestion."""

from __future__ import annotations

import csv
import json
import unicodedata
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any

import ijson

from .models import Book


def stream_catalog(path: Path) -> Generator[dict[str, Any], None, None]:
    """Yield records from CSV, JSON array, or newline-delimited JSON files."""
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
        return
    if suffix not in {".json", ".jsonl", ".ndjson"}:
        raise ValueError("Catalog must be CSV, JSON, JSONL, or NDJSON")

    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line.strip():
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError(f"JSON line {line_number} must contain an object")
                    yield item
        return

    with path.open("rb") as handle:
        has_bom = handle.read(3) == b"\xef\xbb\xbf"
        start = 3 if has_bom else 0
        handle.seek(start)
        first = handle.read(256).lstrip()[:1]
        handle.seek(start)
        prefix = "item" if first == b"[" else "books.item" if first == b"{" else ""
        if not prefix:
            raise ValueError("JSON catalog must be an array or contain a 'books' array")
        for item in ijson.items(handle, prefix):
            if isinstance(item, dict):
                yield item


def normalize_record(record: dict[str, Any]) -> Book:
    def text(*names: str) -> str:
        value = next((record.get(name) for name in names if record.get(name) is not None), "")
        return unicodedata.normalize("NFKC", str(value)).strip()

    def values(*names: str) -> list[str]:
        value: Any = next(
            (record.get(name) for name in names if record.get(name) is not None),
            [],
        )
        candidates = value if isinstance(value, list) else str(value).replace("|", ";").split(";")
        return [
            unicodedata.normalize("NFKC", str(item)).strip()
            for item in candidates
            if str(item).strip()
        ]

    year_text = text("first_publish_year", "year", "publication_year")
    edition_text = text("edition_count", "editions")
    rating_text = text("ratings_average", "rating")
    rating_count_text = text("ratings_count")
    return Book(
        key=text("key", "work_key", "id"),
        title=text("title", "name") or "Untitled",
        authors=values("authors", "author", "author_name"),
        author_keys=values("author_keys", "author_key"),
        first_publish_year=_integer(year_text),
        edition_count=_integer(edition_text) or 0,
        isbn=values("isbn", "isbns", "isbn10", "isbn13"),
        subjects=values("subjects", "subject"),
        languages=values("languages", "language"),
        publisher=text("publisher") or None,
        ratings_average=_float(rating_text),
        ratings_count=_integer(rating_count_text) or 0,
    )


def deduplicate_stream(records: Iterable[dict[str, Any]]) -> Generator[Book, None, None]:
    """Normalize records and suppress duplicates with a Python set."""
    seen: set[str] = set()
    for record in records:
        book = normalize_record(record)
        isbn = book.isbn[0].casefold() if book.isbn else ""
        identity = book.key.casefold() or isbn or f"{book.title}|{book.authors[:1]}".casefold()
        if identity not in seen:
            seen.add(identity)
            yield book


def _integer(value: str) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def _float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
