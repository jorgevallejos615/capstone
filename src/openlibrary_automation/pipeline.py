"""Pandas transformation and analytics pipeline."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .models import Author, Book


BOOK_COLUMNS = [
    "key",
    "title",
    "authors",
    "author_keys",
    "first_publish_year",
    "edition_count",
    "isbn",
    "subjects",
    "languages",
    "publisher",
    "ratings_average",
    "ratings_count",
]


def books_frame(books: Iterable[Book]) -> pd.DataFrame:
    frame = pd.DataFrame([book.as_record() for book in books], columns=BOOK_COLUMNS)
    if frame.empty:
        return frame
    frame["first_publish_year"] = pd.to_numeric(
        frame["first_publish_year"], errors="coerce"
    ).astype("Int64")
    frame["edition_count"] = pd.to_numeric(frame["edition_count"], errors="coerce").fillna(0)
    frame["ratings_count"] = pd.to_numeric(frame["ratings_count"], errors="coerce").fillna(0)
    frame["ratings_average"] = pd.to_numeric(frame["ratings_average"], errors="coerce")
    return frame


def authors_frame(authors: Iterable[Author]) -> pd.DataFrame:
    frame = pd.DataFrame([author.as_record() for author in authors])
    if frame.empty:
        return pd.DataFrame(columns=list(Author.__dataclass_fields__))
    frame["work_count"] = pd.to_numeric(frame["work_count"], errors="coerce").fillna(0)
    frame["ratings_count"] = pd.to_numeric(frame["ratings_count"], errors="coerce").fillna(0)
    frame["ratings_average"] = pd.to_numeric(frame["ratings_average"], errors="coerce")
    return frame


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def deduplicate_catalog(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduplicate by work key, then ISBN, then normalized title/primary author."""
    if frame.empty:
        return frame.copy(), frame.copy()

    working = frame.copy()
    key = working["key"].fillna("").astype(str).str.strip()
    first_isbn = working["isbn"].fillna("").astype(str).str.split(";").str[0].str.strip()
    title_author = (
        working["title"].map(_normalized_text)
        + "|"
        + working["authors"].fillna("").astype(str).str.split(";").str[0].map(_normalized_text)
    )
    working["_identity"] = key.where(
        key.ne(""),
        "isbn:" + first_isbn.where(first_isbn.ne(""), title_author),
    )
    working["_source_order"] = range(len(working))
    working = working.sort_values(
        ["_identity", "edition_count", "ratings_count", "_source_order"],
        ascending=[True, False, False, True],
    )
    duplicate_mask = working.duplicated("_identity", keep="first")
    duplicates = working.loc[duplicate_mask].drop(columns=["_identity", "_source_order"])
    unique = working.loc[~duplicate_mask].sort_values("_source_order")
    unique = unique.drop(columns=["_identity", "_source_order"]).reset_index(drop=True)
    return unique, duplicates.reset_index(drop=True)


def subject_summary(frame: pd.DataFrame, *, top: int = 25) -> pd.DataFrame:
    if frame.empty or "subjects" not in frame:
        return pd.DataFrame(columns=["subject", "book_count", "avg_rating", "total_editions"])
    exploded = frame.assign(
        subject=frame["subjects"].fillna("").astype(str).str.split(r";\s*")
    ).explode("subject")
    exploded["subject"] = exploded["subject"].astype(str).str.strip()
    exploded = exploded[exploded["subject"].ne("")]
    if exploded.empty:
        return pd.DataFrame(columns=["subject", "book_count", "avg_rating", "total_editions"])
    return (
        exploded.groupby("subject", as_index=False)
        .agg(
            book_count=("key", "nunique"),
            avg_rating=("ratings_average", "mean"),
            total_editions=("edition_count", "sum"),
        )
        .sort_values(["book_count", "total_editions"], ascending=False)
        .head(top)
        .reset_index(drop=True)
    )


def author_summary(frame: pd.DataFrame, *, top: int = 25) -> pd.DataFrame:
    if frame.empty or "authors" not in frame:
        return pd.DataFrame(
            columns=["author", "book_count", "total_editions", "avg_rating", "earliest_year"]
        )
    exploded = frame.assign(
        author=frame["authors"].fillna("").astype(str).str.split(r";\s*")
    ).explode("author")
    exploded["author"] = exploded["author"].astype(str).str.strip()
    exploded = exploded[exploded["author"].ne("")]
    if exploded.empty:
        return pd.DataFrame()
    return (
        exploded.groupby("author", as_index=False)
        .agg(
            book_count=("key", "nunique"),
            total_editions=("edition_count", "sum"),
            avg_rating=("ratings_average", "mean"),
            earliest_year=("first_publish_year", "min"),
        )
        .sort_values(["book_count", "total_editions"], ascending=False)
        .head(top)
        .reset_index(drop=True)
    )
