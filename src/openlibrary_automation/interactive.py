"""Loop-driven terminal application required by the capstone."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .client import OpenLibraryError
from .service import ResearchService
from .validation import clean_text, compact_isbn, looks_like_isbn, validate_isbn

console = Console()
RULE = "=" * 70
ENV_KEYS = (
    "OL_BASE_URL",
    "OL_TIMEOUT",
    "OL_CACHE_TTL",
    "OL_CONCURRENCY",
    "OL_MAX_RETRIES",
    "OL_USER_AGENT",
    "OL_CACHE_PATH",
)


def run_menu() -> None:
    """Display the main menu until the user explicitly exits."""
    for key, value in _read_env().items():
        if key in ENV_KEYS:
            os.environ.setdefault(key, value)
    actions = {
        "1": _option_ingest_subject,
        "2": _option_search,
        "3": _option_process_catalog,
        "4": _option_reports,
        "5": _option_administration,
        "6": _option_quality,
    }
    while True:
        console.print(
            f"\n{RULE}\n"
            "[bold cyan]OPEN LIBRARY INTELLIGENCE & CATALOG AUTOMATION SYSTEM[/]\n"
            f"{RULE}\n"
            "[1] Ingest Works by Subject\n"
            "[2] Search Books by Author/Title/ISBN\n"
            "[3] Process & Deduplicate Local Catalog File\n"
            "[4] Generate Analytical Reports\n"
            "[5] System Health, Cache Maintenance & Database Administration\n"
            "[6] Run Quality Checks & Test Suite Diagnostic\n"
            "[0] Exit\n"
            f"{RULE}"
        )
        choice = input("Select an option: ").strip()
        if choice == "0":
            console.print("[green]Goodbye.[/]")
            return
        action = actions.get(choice)
        if action is None:
            console.print("[yellow]Enter a number from 0 through 6.[/]")
            continue
        try:
            action()
        except (OpenLibraryError, ValueError, OSError) as exc:
            console.print(f"[bold red]Operation failed:[/] {exc}")
        except KeyboardInterrupt:
            console.print("\n[yellow]Operation cancelled.[/]")
        _pause()


def _option_ingest_subject() -> None:
    subject = _prompt_clean("Subject: ")
    depth = _prompt_integer("Page depth (1-20): ", minimum=1, maximum=20)

    async def workflow() -> None:
        async with ResearchService() as service:
            datasets, result = await service.ingest_subject_comparison(subject, depth)
        _show_frame(datasets["books"], f"Works for {subject}")
        table = Table(title="Synchronous vs asynchronous runtime")
        table.add_column("Works", justify="right")
        table.add_column("Sync", justify="right")
        table.add_column("Async", justify="right")
        table.add_column("Speedup", justify="right")
        table.add_row(
            str(result.item_count),
            f"{result.sequential_seconds:.3f}s",
            f"{result.concurrent_seconds:.3f}s",
            f"{result.speedup:.2f}x",
        )
        console.print(table)
        console.print("[green]Retrieved works were upserted into SQLite.[/]")

    asyncio.run(workflow())


def _option_search() -> None:
    query = _prompt_clean("Author, title, or ISBN: ")
    if looks_like_isbn(query):
        if not validate_isbn(query):
            raise ValueError("ISBN checksum is invalid")
        query = compact_isbn(query)
    limit = _prompt_integer("Maximum results (1-100): ", minimum=1, maximum=100, default=25)

    async def workflow() -> None:
        async with ResearchService() as service:
            frame, source = await service.search_local_then_api(query, limit)
        _show_frame(frame, f"Search results from {source}")

    asyncio.run(workflow())


def _option_process_catalog() -> None:
    raw_path = input("CSV/JSON catalog path: ").strip().strip('"')
    if not raw_path:
        raise ValueError("A file path is required")
    path = Path(raw_path).expanduser()

    async def workflow() -> None:
        async with ResearchService() as service:
            count = await service.ingest_catalog_file(path)
        console.print(f"[green]Upserted {count} unique normalized record(s).[/]")

    asyncio.run(workflow())


def _option_reports() -> None:
    raw_path = input("Output directory [reports]: ").strip()
    output_dir = Path(raw_path or "reports").expanduser()

    async def workflow() -> None:
        async with ResearchService() as service:
            paths = await service.generate_library_reports(output_dir)
        console.print("[green]Generated reports:[/]")
        for path in paths:
            console.print(f"  - {path}")

    asyncio.run(workflow())


def _option_administration() -> None:
    while True:
        console.print(
            "\n[1] Database metrics\n[2] Purge expired cache\n"
            "[3] Show runtime/.env configuration\n[4] Set .env value\n"
            "[5] Remove .env value\n[0] Back"
        )
        choice = input("Administration option: ").strip()
        if choice == "0":
            return
        if choice == "1":
            asyncio.run(_show_metrics())
        elif choice == "2":
            asyncio.run(_purge_expired())
        elif choice == "3":
            _show_environment()
        elif choice == "4":
            _set_environment()
        elif choice == "5":
            _remove_environment()
        else:
            console.print("[yellow]Enter a number from 0 through 5.[/]")


async def _show_metrics() -> None:
    async with ResearchService() as service:
        metrics = await service.cache.database_metrics()
        database = service.settings.cache_path
    table = Table(title=f"SQLite metrics — {database}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for name, value in metrics.items():
        display = f"{value:.2%}" if name == "cache_hit_ratio" else str(value)
        table.add_row(name.replace("_", " ").title(), display)
    console.print(table)


async def _purge_expired() -> None:
    async with ResearchService() as service:
        count = await service.cache.purge_expired()
    console.print(f"[green]Purged {count} expired cache record(s).[/]")


def _show_environment() -> None:
    stored = _read_env()
    table = Table(title="Configuration state")
    table.add_column("Variable")
    table.add_column("Runtime value")
    table.add_column("Stored in .env")
    for key in ENV_KEYS:
        runtime = os.getenv(key, "")
        if "USER_AGENT" in key:
            runtime = runtime or "(default)"
        table.add_row(key, runtime or "(default)", "yes" if key in stored else "no")
    console.print(table)


def _set_environment() -> None:
    key = input(f"Variable ({', '.join(ENV_KEYS)}): ").strip().upper()
    if key not in ENV_KEYS:
        raise ValueError("Unknown configuration variable")
    value = clean_text(input("Value: "), max_length=500)
    if not value:
        raise ValueError("Value cannot be empty")
    values = _read_env()
    values[key] = value
    _write_env(values)
    os.environ[key] = value
    console.print(f"[green]{key} updated for this process and .env.[/]")


def _remove_environment() -> None:
    key = input("Variable to remove: ").strip().upper()
    if key not in ENV_KEYS:
        raise ValueError("Unknown configuration variable")
    values = _read_env()
    values.pop(key, None)
    _write_env(values)
    os.environ.pop(key, None)
    console.print(f"[green]{key} removed.[/]")


def _read_env() -> dict[str, str]:
    path = Path(".env")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _write_env(values: dict[str, str]) -> None:
    content = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    Path(".env").write_text(content, encoding="utf-8")


def _option_quality() -> None:
    checks = [
        ("ruff check", [sys.executable, "-m", "ruff", "check", "."]),
        ("ruff format --check", [sys.executable, "-m", "ruff", "format", "--check", "."]),
        ("pytest", [sys.executable, "-m", "pytest"]),
    ]
    for label, command in checks:
        console.print(Panel.fit(f"[bold]{label}[/]"))
        completed = subprocess.run(command, check=False, text=True, capture_output=True)
        output = (completed.stdout + completed.stderr).strip()
        console.print(output or "(no output)")
        status = "[green]PASS[/]" if completed.returncode == 0 else "[red]FAIL[/]"
        console.print(f"{label}: {status} (exit {completed.returncode})")


def _prompt_clean(prompt: str) -> str:
    while True:
        value = clean_text(input(prompt))
        if len(value) >= 2:
            return value
        console.print("[yellow]Enter at least two valid characters.[/]")


def _prompt_integer(
    prompt: str,
    *,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        if raw.isdigit() and minimum <= int(raw) <= maximum:
            return int(raw)
        console.print(f"[yellow]Enter a whole number from {minimum} through {maximum}.[/]")


def _show_frame(frame: pd.DataFrame, title: str, rows: int = 20) -> None:
    console.print(f"\n[bold cyan]{title}[/] — {len(frame)} row(s)")
    if frame.empty:
        console.print("[yellow]No results found.[/]")
        return
    columns = [
        column
        for column in ("title", "authors", "first_publish_year", "edition_count", "isbn")
        if column in frame.columns
    ]
    table = Table(show_lines=True)
    for column in columns:
        table.add_column(column.replace("_", " ").title(), max_width=38, overflow="fold")
    for _, row in frame.head(rows).iterrows():
        table.add_row(*(str(row[column]) if not pd.isna(row[column]) else "" for column in columns))
    console.print(table)


def _pause() -> None:
    input("\nPress Enter to return to the main menu...")
