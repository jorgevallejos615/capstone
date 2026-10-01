# Open Library Automation

A production-oriented Python 3.11+ CLI for asynchronous research against the public
[Open Library API](https://openlibrary.org/developers/api). It combines a polite, retrying HTTP
client with persistent SQLite caching, pandas analytics, catalog deduplication, report generation,
and both command-driven and interactive terminal interfaces.

## Features

- Book and author search
- Subject-level book, author, and subject aggregation
- Multi-query catalog construction and deterministic deduplication
- CSV, Excel, and JSON reports
- SQLite response cache with TTL and WAL mode
- Bounded concurrent requests with retry/backoff for transient failures
- Uncached sequential-versus-concurrent performance benchmark
- Rich terminal previews and interactive menu

## Install

```bash
uv sync --extra dev
uv run python main.py
```

The second command opens the loop-driven menu used for subject ingestion and sync/async timing,
local-first validated search, dirty catalog ingestion, analytical reports, administration, and
quality diagnostics.

Use a descriptive contact address in the user agent when running sustained research:

```powershell
$env:OL_USER_AGENT = "my-research-tool/1.0 (contact: you@example.com)"
```

## Commands

```bash
openlibrary search "data science" --limit 100 --format csv
openlibrary subject "science fiction" --limit 100 --format xlsx
openlibrary authors "Ursula Le Guin" --format json
openlibrary catalog -q "climate change" -q "global warming" --format xlsx
openlibrary benchmark "python programming" --count 10
openlibrary cache stats
openlibrary menu
```

Reports are written to `reports/` by default. Use `--output PATH` to select a destination.
For a multi-table CSV report, one file is emitted per dataset. Excel stores each dataset on a
separate worksheet, while JSON stores datasets as top-level properties.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OL_BASE_URL` | `https://openlibrary.org` | API base URL |
| `OL_TIMEOUT` | `20` | Request timeout in seconds |
| `OL_CACHE_TTL` | `86400` | Cache lifetime in seconds |
| `OL_CONCURRENCY` | `5` | Maximum simultaneous requests |
| `OL_MAX_RETRIES` | `3` | Transient-failure retries |
| `OL_USER_AGENT` | package identifier | HTTP user agent |
| `OL_CACHE_PATH` | OS user cache directory | SQLite database path |

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests mock the API and do not require network access. Live commands do require internet access and
are subject to Open Library availability. The benchmark intentionally bypasses the local cache;
run it sparingly because it performs each work request twice.
