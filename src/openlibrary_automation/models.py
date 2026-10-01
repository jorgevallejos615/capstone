"""Typed domain models normalized from Open Library responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _first(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


@dataclass(slots=True)
class Book:
    key: str
    title: str
    authors: list[str] = field(default_factory=list)
    author_keys: list[str] = field(default_factory=list)
    first_publish_year: int | None = None
    edition_count: int = 0
    isbn: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    publisher: str | None = None
    ratings_average: float | None = None
    ratings_count: int = 0

    @classmethod
    def from_search_doc(cls, doc: dict[str, Any]) -> Book:
        return cls(
            key=str(doc.get("key", "")),
            title=str(doc.get("title", "Untitled")),
            authors=[str(value) for value in doc.get("author_name", [])],
            author_keys=[str(value) for value in doc.get("author_key", [])],
            first_publish_year=doc.get("first_publish_year"),
            edition_count=int(doc.get("edition_count") or 0),
            isbn=[str(value) for value in doc.get("isbn", [])],
            subjects=[str(value) for value in doc.get("subject", [])],
            languages=[str(value) for value in doc.get("language", [])],
            publisher=_first(doc.get("publisher")),
            ratings_average=doc.get("ratings_average"),
            ratings_count=int(doc.get("ratings_count") or 0),
        )

    @classmethod
    def from_subject_work(cls, work: dict[str, Any]) -> Book:
        authors = work.get("authors", [])
        return cls(
            key=str(work.get("key", "")),
            title=str(work.get("title", "Untitled")),
            authors=[str(item.get("name", "")) for item in authors if item.get("name")],
            author_keys=[
                str(item.get("key", "")).removeprefix("/authors/")
                for item in authors
                if item.get("key")
            ],
            first_publish_year=work.get("first_publish_year"),
            edition_count=int(work.get("edition_count") or 0),
            subjects=[str(value) for value in work.get("subject", [])],
        )

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("authors", "author_keys", "isbn", "subjects", "languages"):
            record[key] = "; ".join(record[key])
        return record


@dataclass(slots=True)
class Author:
    key: str
    name: str
    birth_date: str | None = None
    death_date: str | None = None
    top_work: str | None = None
    work_count: int = 0
    ratings_average: float | None = None
    ratings_count: int = 0

    @classmethod
    def from_search_doc(cls, doc: dict[str, Any]) -> Author:
        return cls(
            key=str(doc.get("key", "")).removeprefix("/authors/"),
            name=str(doc.get("name", "Unknown")),
            birth_date=doc.get("birth_date"),
            death_date=doc.get("death_date"),
            top_work=doc.get("top_work"),
            work_count=int(doc.get("work_count") or 0),
            ratings_average=doc.get("ratings_average"),
            ratings_count=int(doc.get("ratings_count") or 0),
        )

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    item_count: int
    sequential_seconds: float
    concurrent_seconds: float

    @property
    def speedup(self) -> float:
        return (
            self.sequential_seconds / self.concurrent_seconds
            if self.concurrent_seconds > 0
            else 0.0
        )
