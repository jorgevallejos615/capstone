import json
from pathlib import Path

import pandas as pd

from openlibrary_automation.reports import write_report


def test_json_report_contains_named_datasets(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    paths = write_report(
        {"books": pd.DataFrame([{"title": "Example", "year": 2020}])},
        output,
    )

    assert paths == [output]
    assert json.loads(output.read_text(encoding="utf-8"))["books"][0]["title"] == "Example"


def test_multi_dataset_csv_creates_separate_files(tmp_path: Path) -> None:
    paths = write_report(
        {
            "books": pd.DataFrame([{"title": "Example"}]),
            "author stats": pd.DataFrame([{"name": "Ada"}]),
        },
        tmp_path / "report.csv",
    )

    assert {path.name for path in paths} == {"report_books.csv", "report_author_stats.csv"}


def test_excel_has_one_sheet_per_dataset(tmp_path: Path) -> None:
    output = tmp_path / "report.xlsx"
    write_report({"books": pd.DataFrame([{"title": "Example"}])}, output)

    assert pd.ExcelFile(output).sheet_names == ["books"]
