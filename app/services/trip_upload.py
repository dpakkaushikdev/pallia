"""Convert supported trip upload files to the UTF-8 CSV used by billing."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import load_workbook


def trip_upload_as_csv(contents: bytes, filename: str | None) -> bytes:
    """Return CSV bytes for a CSV, XLSX, or XLSM trip upload.

    For workbooks, the active (first selected) sheet is converted. The billing
    parser and all downstream calculations continue to receive ordinary CSV.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv":
        return contents
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError("Upload a .csv, .xlsx, or .xlsm trip file.")

    try:
        workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Could not read this Excel workbook. Please open it in Excel and save it as .xlsx, then upload again.") from exc

    try:
        sheet = workbook.active
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        for row in sheet.iter_rows(values_only=True):
            if any(value is not None for value in row):
                writer.writerow([_excel_value(value) for value in row])
        converted = output.getvalue().encode("utf-8-sig")
    finally:
        workbook.close()

    if not converted.strip():
        raise ValueError("The first worksheet in this Excel workbook is empty.")
    return converted


def _excel_value(value):
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time.min else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, timedelta):
        minutes = int(value.total_seconds() // 60)
        return f"{minutes // 60}:{minutes % 60:02d}"
    return value
