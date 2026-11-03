"""MMT portal export -> MIS trips, and MIS trips -> Excel in the MMT_MIS layout."""
from __future__ import annotations

import io
import re
from datetime import date, datetime, time
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from app.services.eee_taxi_mis import DATE_KEYS, MIS_COLUMNS, NUMBER_KEYS, TIME_KEYS, MisTrip, parse_trip

MMT_REQUIRED = {"External Id", "Vehicle Registration", "Driver Name", "Driver Phone",
                "Actual End Date", "Actual End Time", "Package", "Total Bill Amount"}
MAX_IMPORT_ROWS = 2000
AIRPORT = "Airport"
# Pincode prefixes, then place names, for the non-airport end of a transfer.
ZONE_PINCODES = {"110": "Delhi", "122": "Gurgaon", "201": "Noida"}
ZONE_WORDS = {"gurgaon": "Gurgaon", "gurugram": "Gurgaon", "noida": "Noida", "delhi": "Delhi"}


def _text(value) -> str:
    """MMT sends every cell as text; a file re-saved in Excel may hold real dates."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value).strip()
    return "" if text == "-" else text


def guess_zone(address: str) -> str:
    pincode = re.search(r"\b(\d{3})\d{3}\b", address or "")
    if pincode and pincode[1] in ZONE_PINCODES:
        return ZONE_PINCODES[pincode[1]]
    found = {zone for word, zone in ZONE_WORDS.items() if word in (address or "").lower()}
    return found.pop() if len(found) == 1 else ""


def mmt_row_to_trip(raw: dict[str, str], dispatcher: str) -> dict[str, str]:
    """Map one 'Trip Report' row onto MIS keys (Pick up Time is the scheduled time)."""
    package = raw.get("Package", "")
    pickup, drop = raw.get("Pickup Address", ""), raw.get("Destination", "")
    to_airport = "to airport" in package.lower()
    from_airport = package.lower().startswith("airport")
    return {
        "date": raw.get("Scheduled Start Date") or raw.get("Actual Start Date", ""),
        "dispatcher_name": dispatcher,
        "driver_name": raw.get("Driver Name", ""),
        "driver_contact": raw.get("Driver Phone", ""),
        "cab_no": raw.get("Vehicle Registration", ""),
        "route_no": raw.get("External Id", ""),
        "company_name": "MMT",
        "guest_name": raw.get("Passenger Name", ""),
        "pickup_address": pickup,
        "pickup_zone": AIRPORT if from_airport else guess_zone(pickup),
        "pickup_time": raw.get("Scheduled Start Time") or raw.get("Actual Start Time", ""),
        "drop_location": drop,
        "drop_zone": AIRPORT if to_airport else guess_zone(drop),
        "drop_time": raw.get("Actual End Time", ""),
        "trip_end_date": raw.get("Actual End Date", ""),
        "total_kms": raw.get("Total Distance", ""),
        "package": package,
        "trip_fare": raw.get("Total Bill Amount", ""),
        "parking": raw.get("Parking", ""),
        "toll": raw.get("Toll", ""),
        "remarks": raw.get("Remarks", ""),
        "mmt_base_cost": raw.get("Base Cost", ""),
        "mmt_prepaid_amount": raw.get("Prepaid Amount", ""),
    }


def parse_mmt_export(data: bytes, dispatcher: str) -> tuple[list[MisTrip], list[str]]:
    """Valid trips plus one message per row that could not be read."""
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Upload the MMT Trip Report as an .xlsx file.") from exc
    sheets = sorted(workbook.worksheets, key=lambda ws: ws.title != "Trip Report")
    for sheet in sheets:
        rows = sheet.iter_rows(values_only=True)
        headers = [_text(h) for h in next(rows, ())]
        if MMT_REQUIRED <= set(headers):
            break
    else:
        raise ValueError("This is not an MMT Trip Report: columns such as External Id and Total Bill Amount are missing.")
    trips, errors = [], []
    for line, values in enumerate(rows, 2):
        raw = {header: _text(value) for header, value in zip(headers, values) if header}
        if not any(raw.values()):
            continue
        if len(trips) + len(errors) >= MAX_IMPORT_ROWS:
            raise ValueError(f"Upload at most {MAX_IMPORT_ROWS} trips at a time.")
        try:
            trips.append(parse_trip(mmt_row_to_trip(raw, dispatcher)))
        except ValueError as exc:
            errors.append(f"Row {line}: {exc}")
    return trips, errors


def _cell_value(key: str, value: str):
    if not value:
        return None
    if key in DATE_KEYS:
        return date.fromisoformat(value)
    if key in TIME_KEYS:
        return time.fromisoformat(value)
    if key in NUMBER_KEYS:
        number = Decimal(value)
        return int(number) if number == number.to_integral_value() else float(number)
    return value


def build_mis_workbook(trips: list[dict]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append([header for _, header in MIS_COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="FFFF00")
    for trip in trips:
        sheet.append([_cell_value(key, trip.get(key, "")) for key, _ in MIS_COLUMNS])
    for index, (key, header) in enumerate(MIS_COLUMNS, 1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = max(12, min(len(header) + 4, 40))
        fmt = "dd-mm-yyyy" if key in DATE_KEYS else "hh:mm" if key in TIME_KEYS else None
        if fmt:
            for cell in sheet[letter][1:]:
                cell.number_format = fmt
    sheet.freeze_panes = "A2"
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()
