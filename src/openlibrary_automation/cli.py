"""Typer command line and interactive terminal menu."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

from .client import OpenLibraryError
from .reports import ReportError, write_report
from .service import ResearchService

app = typer.Typer(
    name="openlibrary",
    help="Research and analyze the Open Library catalog.",
    no_args_is_help=True,
)
cache_app = typer.Typer(help="Inspect or clear the local response cache.")
app.add_typer(cache_app, name="cache")
console = Console()


def _run(awaitable: Awaitable[Any]) -> Any:
    try:
        return asyncio.run(awaitable)
    except (OpenLibraryError, ReportError, ValueError) as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(1) from exc


def _default_output(label: str, fmt: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "report"
    extension = "xlsx" if fmt in {"xlsx", "excel"} else fmt
    return Path("reports") / f"{slug}_{timestamp}.{extension}"


def _show_frame(frame: pd.DataFrame, title: str, rows: int = 15) -> None:
    console.print(f"\n[bold cyan]{title}[/] — {len(frame)} row(s)")
    if frame.empty:
        console.print("[yellow]No results found.[/]")
        return
    preview = frame.head(rows)
    preferred = [
        column
        for column in ("title", "authors", "name", "author", "subject", "first_publish_year",
                       "edition_count", "work_count", "book_count", "avg_rating")
        if column in preview.columns
    ]
    columns = preferred or list(preview.columns[:6])
    table = Table(show_lines=False)
    for column in columns:
        table.add_column(str(column).replace("_", " ").title(), overflow="fold", max_width=35)
    for _, row in preview.iterrows():
        table.add_row(*(("" if pd.isna(row[col]) else str(row[col])) for col in columns))
    console.print(table)


async def _search_workflow(query: str, limit: int, output: Path | None, fmt: str) -> None:
    async with ResearchService() as service:
        frame = await service.search_books(query, limit)
    _show_frame(frame, f"Books matching “{query}”")
    target = output or _default_output(f"search_{query}", fmt)
    paths = write_report({"books": frame}, target, report_format=fmt)
    console.print(f"[green]Report written:[/] {', '.join(map(str, paths))}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Title, author, ISBN, or free-text query."),
    limit: int = typer.Option(50, min=1, max=1000, help="Maximum books."),
    output: Path | None = typer.Option(None, "-o", "--output", help="Report path."),
    fmt: str = typer.Option("csv", "--format", help="csv, xlsx, or json."),
) -> None:
    """Search books and generate a normalized report."""
    _run(_search_workflow(query, limit, output, fmt))


async def _subject_workflow(subject: str, limit: int, output: Path | None, fmt: str) -> None:
    async with ResearchService() as service:
        datasets = await service.research_subject(subject, limit)
    _show_frame(datasets["books"], f"Subject: {subject}")
    paths = write_report(
        datasets,
        output or _default_output(f"subject_{subject}", fmt),
        report_format=fmt,
    )
    console.print(f"[green]Report written:[/] {', '.join(map(str, paths))}")


@app.command()
def subject(
    name: str = typer.Argument(..., help="Subject name, such as science fiction."),
    limit: int = typer.Option(50, min=1, max=1000),
    output: Path | None = typer.Option(None, "-o", "--output"),
    fmt: str = typer.Option("xlsx", "--format", help="csv, xlsx, or json."),
) -> None:
    """Aggregate books and authors for a subject."""
    _run(_subject_workflow(name, limit, output, fmt))


async def _authors_workflow(query: str, limit: int, output: Path | None, fmt: str) -> None:
    async with ResearchService() as service:
        frame = await service.research_authors(query, limit)
    _show_frame(frame, f"Authors matching “{query}”")
    paths = write_report(
        {"authors": frame},
        output or _default_output(f"authors_{query}", fmt),
        report_format=fmt,
    )
    console.print(f"[green]Report written:[/] {', '.join(map(str, paths))}")


@app.command()
def authors(
    query: str = typer.Argument(..., help="Author name."),
    limit: int = typer.Option(25, min=1, max=100),
    output: Path | None = typer.Option(None, "-o", "--output"),
    fmt: str = typer.Option("csv", "--format", help="csv, xlsx, or json."),
) -> None:
    """Rank matching authors by works and ratings."""
    _run(_authors_workflow(query, limit, output, fmt))


async def _catalog_workflow(
    queries: list[str], limit: int, output: Path | None, fmt: str
) -> None:
    async with ResearchService() as service:
        datasets = await service.build_catalog(queries, limit)
    _show_frame(datasets["catalog"], "Deduplicated catalog")
    console.print(f"[yellow]Duplicates removed:[/] {len(datasets['duplicates'])}")
    paths = write_report(
        datasets,
        output or _default_output("catalog", fmt),
        report_format=fmt,
    )
    console.print(f"[green]Report written:[/] {', '.join(map(str, paths))}")


@app.command()
def catalog(
    query: list[str] = typer.Option(
        ...,
        "--query",
        "-q",
        help="Repeat for each search query to combine.",
    ),
    limit: int = typer.Option(50, min=1, max=1000, help="Results per query."),
    output: Path | None = typer.Option(None, "-o", "--output"),
    fmt: str = typer.Option("xlsx", "--format", help="csv, xlsx, or json."),
) -> None:
    """Combine searches, deduplicate records, and produce analytics."""
    _run(_catalog_workflow(query, limit, output, fmt))


async def _benchmark_workflow(query: str, count: int) -> None:
    async with ResearchService() as service:
        result = await service.benchmark(query, count)
    table = Table(title="Sequential vs concurrent fetching")
    table.add_column("Works", justify="right")
    table.add_column("Sequential", justify="right")
    table.add_column("Concurrent", justify="right")
    table.add_column("Speedup", justify="right")
    table.add_row(
        str(result.item_count),
        f"{result.sequential_seconds:.3f}s",
        f"{result.concurrent_seconds:.3f}s",
        f"{result.speedup:.2f}×",
    )
    console.print(table)


@app.command()
def benchmark(
    query: str = typer.Argument("python", help="Query used to select works."),
    count: int = typer.Option(10, min=2, max=50, help="Works fetched in each run."),
) -> None:
    """Compare uncached sequential and bounded-concurrent requests."""
    _run(_benchmark_workflow(query, count))


async def _cache_stats() -> None:
    async with ResearchService() as service:
        count = await service.cache.count()
        console.print(f"Cached responses: [bold]{count}[/]")
        console.print(f"Database: {service.settings.cache_path}")


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache location and entry count."""
    _run(_cache_stats())


async def _cache_clear() -> None:
    async with ResearchService() as service:
        count = await service.cache.clear()
    console.print(f"[green]Deleted {count} cached response(s).[/]")


@cache_app.command("clear")
def cache_clear(force: bool = typer.Option(False, "--force", help="Skip confirmation.")) -> None:
    """Delete every cached API response."""
    if force or typer.confirm("Clear the Open Library cache?"):
        _run(_cache_clear())


@app.command()
def menu() -> None:
    """Launch an interactive terminal menu."""
    while True:
        console.print(
            "\n[bold cyan]Open Library Automation[/]\n"
            "1. Search books\n2. Research a subject\n3. Analyze authors\n"
            "4. Build a deduplicated catalog\n5. Run fetch benchmark\n6. Cache stats\n0. Exit"
        )
        choice = Prompt.ask("Choose", choices=["0", "1", "2", "3", "4", "5", "6"])
        if choice == "0":
            return
        if choice == "1":
            search(Prompt.ask("Search query"), IntPrompt.ask("Limit", default=50), None, "csv")
        elif choice == "2":
            subject(Prompt.ask("Subject"), IntPrompt.ask("Limit", default=50), None, "xlsx")
        elif choice == "3":
            authors(Prompt.ask("Author query"), IntPrompt.ask("Limit", default=25), None, "csv")
        elif choice == "4":
            queries = [
                value.strip()
                for value in Prompt.ask("Queries (comma-separated)").split(",")
            ]
            catalog(queries, IntPrompt.ask("Limit per query", default=50), None, "xlsx")
        elif choice == "5":
            benchmark(
                Prompt.ask("Selection query", default="python"),
                IntPrompt.ask("Count", default=10),
            )
        elif choice == "6":
            cache_stats()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logs."),
) -> None:
    """Automate book research with the public Open Library API."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
