"""Excel download of a master list (drivers, vehicles): one header row, then one row per entry."""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.eee_taxi_mis_query import ist_today
from app.services.eee_taxi_vehicles import CSV_FIELDS

HEADER_FILL = PatternFill("solid", fgColor="FFE600")
MIN_WIDTH, MAX_WIDTH = 10, 45
TEXT_FORMAT = "@"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DRIVER_COLUMNS = [
    ("name", "Name"), ("truein_id", "Truein ID"), ("mobile", "Mobile"), ("alternate_mobile", "Alternate mobile"),
    ("father_name", "Father name"), ("email", "Email"), ("status", "Status"), ("availability", "Availability"),
    ("branch", "Branch"), ("department", "Department"), ("date_of_joining", "Date of joining"),
    ("dl_number", "DL number"), ("dl_expiry", "DL expiry"), ("emergency_contact_name", "Emergency contact name"),
    ("emergency_contact_number", "Emergency contact number"),
    ("emergency_contact_relationship", "Emergency contact relationship"),
]
# The same headers Import vehicle CSV reads, so a download saved as CSV can be imported again.
VEHICLE_COLUMNS = list(CSV_FIELDS.items())


def download_name(master: str) -> str:
    """'Driver_Master_2026-11-26.xlsx': the day (India time) the copy was taken."""
    return f"{master}_Master_{ist_today():%Y-%m-%d}.xlsx"


def build_master_workbook(title: str, columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    """Every value is written as text: a mobile keeps its leading zero and nothing runs as a formula."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append([header for _, header in columns])
    for cell in sheet[1]:
        cell.font = Font(name="Arial", size=10, bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row_number, row in enumerate(rows, 2):
        for column_number, (key, _) in enumerate(columns, 1):
            value = str(row.get(key) or "")
            if not value:
                continue
            cell = sheet.cell(row=row_number, column=column_number, value=value)
            cell.data_type = "s"      # openpyxl would otherwise read "=..." as a formula
            cell.number_format = TEXT_FORMAT
    for column_number, (key, header) in enumerate(columns, 1):
        longest = max([len(header), *(len(str(row.get(key) or "")) for row in rows)])
        sheet.column_dimensions[get_column_letter(column_number)].width = min(max(longest + 2, MIN_WIDTH), MAX_WIDTH)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()
