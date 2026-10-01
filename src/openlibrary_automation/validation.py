"""Input cleaning and ISBN validation."""

from __future__ import annotations

import re
import unicodedata


def clean_text(value: str, *, max_length: int = 200) -> str:
    """Normalize Unicode and remove control/punctuation characters unsafe for queries."""
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = re.sub(r"[^\w\s\-:'.,/@()&+]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()[:max_length]


def compact_isbn(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value).upper()


def looks_like_isbn(value: str) -> bool:
    compact = compact_isbn(value)
    return len(compact) in {10, 13} and bool(re.fullmatch(r"\d{9}[\dX]|\d{13}", compact))


def validate_isbn(value: str) -> bool:
    compact = compact_isbn(value)
    if len(compact) == 10 and re.fullmatch(r"\d{9}[\dX]", compact):
        total = sum(
            (10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(compact)
        )
        return total % 11 == 0
    if len(compact) == 13 and compact.isdigit():
        total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(compact))
        return total % 10 == 0
    return False
