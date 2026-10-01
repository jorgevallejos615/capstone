from openlibrary_automation.validation import (
    clean_text,
    compact_isbn,
    looks_like_isbn,
    validate_isbn,
)


def test_clean_text_normalizes_and_removes_controls() -> None:
    assert clean_text("  Sci\u0000ence   Fiction!!! ") == "Sci ence Fiction"


def test_validates_isbn_10_and_13_checksums() -> None:
    assert validate_isbn("0-306-40615-2")
    assert validate_isbn("978-0-306-40615-7")
    assert not validate_isbn("978-0-306-40615-8")
    assert looks_like_isbn("0-306-40615-2")
    assert compact_isbn("0-306-40615-x") == "030640615X"
