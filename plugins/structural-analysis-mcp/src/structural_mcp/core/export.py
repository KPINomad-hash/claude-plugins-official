"""Write result rows to CSV or XLSX inside the allowed folder."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from . import errors
from .errors import AdapterError


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for key in r:
            if key not in cols:
                cols.append(key)
    return cols


def write_rows(path: Path, rows: list[dict[str, Any]], header_notes: dict[str, Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = _columns(rows)
    if path.suffix.lower() == ".csv":
        # utf-8-sig so Excel on Windows detects the encoding; notes go to a side file
        # to keep the CSV machine-readable.
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        notes = path.with_suffix(".meta.txt")
        notes.write_text("\n".join(f"{k}: {v}" for k, v in header_notes.items()), encoding="utf-8")
    elif path.suffix.lower() == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "XLSX export needs the 'openpyxl' package.",
                               "Use format='csv' or install openpyxl.") from None
        wb = Workbook()
        ws = wb.active
        ws.title = "results"
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
        meta = wb.create_sheet("meta")
        for k, v in header_notes.items():
            meta.append([k, str(v)])
        wb.save(path)
    else:
        raise AdapterError(errors.INVALID_INPUT, f"Unsupported export format '{path.suffix}'.", "Use csv or xlsx.")
    return len(rows)
