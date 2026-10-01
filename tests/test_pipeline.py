import pandas as pd

from openlibrary_automation.models import Book
from openlibrary_automation.pipeline import (
    author_summary,
    books_frame,
    deduplicate_catalog,
    subject_summary,
)


def sample_books() -> list[Book]:
    return [
        Book(
            key="/works/OL1W",
            title="Example",
            authors=["Ada Author"],
            first_publish_year=2000,
            edition_count=3,
            subjects=["Python", "Programming"],
            ratings_average=4.0,
        ),
        Book(
            key="/works/OL1W",
            title="Example: Another Edition",
            authors=["Ada Author"],
            first_publish_year=2001,
            edition_count=1,
            subjects=["Python"],
            ratings_average=3.0,
        ),
        Book(
            key="/works/OL2W",
            title="Data",
            authors=["Grace Writer"],
            first_publish_year=1999,
            edition_count=2,
            subjects=["Programming"],
            ratings_average=5.0,
        ),
    ]


def test_deduplicate_keeps_strongest_record() -> None:
    unique, duplicates = deduplicate_catalog(books_frame(sample_books()))

    assert len(unique) == 2
    assert len(duplicates) == 1
    assert unique.loc[unique["key"] == "/works/OL1W", "edition_count"].item() == 3


def test_aggregations() -> None:
    frame = books_frame(sample_books())
    subjects = subject_summary(frame)
    authors = author_summary(frame)

    assert subjects.iloc[0]["book_count"] == 2
    assert set(authors["author"]) == {"Ada Author", "Grace Writer"}


def test_empty_frames_are_supported() -> None:
    empty = books_frame([])
    unique, duplicates = deduplicate_catalog(empty)

    assert isinstance(unique, pd.DataFrame)
    assert unique.empty and duplicates.empty
