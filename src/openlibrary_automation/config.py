"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str = "https://openlibrary.org"
    timeout_seconds: float = 20.0
    cache_ttl_seconds: int = 86_400
    max_concurrency: int = 5
    max_retries: int = 3
    user_agent: str = "openlibrary-automation/1.0 (educational research tool)"
    cache_path: Path = user_cache_path("openlibrary-automation") / "cache.sqlite3"

    @classmethod
    def from_env(cls) -> Settings:
        """Create settings from OL_* environment variables."""
        defaults = cls()
        return cls(
            base_url=os.getenv("OL_BASE_URL", defaults.base_url),
            timeout_seconds=float(os.getenv("OL_TIMEOUT", defaults.timeout_seconds)),
            cache_ttl_seconds=int(os.getenv("OL_CACHE_TTL", defaults.cache_ttl_seconds)),
            max_concurrency=int(os.getenv("OL_CONCURRENCY", defaults.max_concurrency)),
            max_retries=int(os.getenv("OL_MAX_RETRIES", defaults.max_retries)),
            user_agent=os.getenv("OL_USER_AGENT", defaults.user_agent),
            cache_path=Path(os.getenv("OL_CACHE_PATH", str(defaults.cache_path))),
        )
