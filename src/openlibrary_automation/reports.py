"""CSV, Excel, and JSON report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class ReportError(ValueError):
    """Raised for unsupported or invalid report requests."""


def write_report(
    datasets: dict[str, pd.DataFrame],
    output: Path,
    *,
    report_format: str | None = None,
) -> list[Path]:
    if not datasets:
        raise ReportError("At least one dataset is required")

    fmt = (report_format or output.suffix.lstrip(".") or "csv").lower()
    if fmt == "excel":
        fmt = "xlsx"
    suffix = {"csv": ".csv", "xlsx": ".xlsx", "json": ".json"}.get(fmt)
    if suffix is None:
        raise ReportError("Format must be csv, xlsx, or json")
    if output.suffix.lower() != suffix:
        output = output.with_suffix(suffix)
    output.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "xlsx":
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for name, frame in datasets.items():
                frame.to_excel(writer, sheet_name=name[:31], index=False)
        return [output]

    if fmt == "json":
        serializable = {
            name: json.loads(frame.to_json(orient="records", date_format="iso"))
            for name, frame in datasets.items()
        }
        output.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return [output]

    paths: list[Path] = []
    multiple = len(datasets) > 1
    for name, frame in datasets.items():
        path = (
            output.with_name(f"{output.stem}_{_safe_name(name)}{output.suffix}")
            if multiple
            else output
        )
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        paths.append(path)
    return paths


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_")
