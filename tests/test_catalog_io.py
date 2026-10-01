import json
from pathlib import Path

from openlibrary_automation.catalog_io import deduplicate_stream, stream_catalog


def test_streams_and_deduplicates_csv(tmp_path: Path) -> None:
    path = tmp_path / "dirty.csv"
    path.write_text(
        "key,title,author,isbn,year\n"
        "/works/OL1W,Café Book,Ada,9780306406157,2001\n"
        "/works/OL1W,Café Book Duplicate,Ada,9780306406157,2001\n"
        "/works/OL2W,Second Book,Grace,,2002\n",
        encoding="utf-8",
    )

    books = list(deduplicate_stream(stream_catalog(path)))

    assert len(books) == 2
    assert books[0].title == "Café Book"
    assert books[0].authors == ["Ada"]


def test_streams_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "catalog.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"key": "/works/OL1W", "title": "One"}),
                json.dumps({"key": "/works/OL2W", "title": "Two"}),
            ]
        ),
        encoding="utf-8",
    )

    assert [book.title for book in deduplicate_stream(stream_catalog(path))] == ["One", "Two"]


def test_streams_json_array_with_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            [
                {"key": "/works/OL1W", "title": "One"},
                {"key": "/works/OL2W", "title": "Two"},
            ]
        ),
        encoding="utf-8-sig",
    )

    assert [record["title"] for record in stream_catalog(path)] == ["One", "Two"]
