"""MMT portal export -> MIS trips, and MIS trips -> Excel in the MMT_MIS layout."""
from __future__ import annotations

import io
import re
from datetime import date, datetime, time
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from app.services.eee_taxi_mis import DATE_KEYS, MIS_COLUMNS, NUMBER_KEYS, TIME_KEYS, MisTrip, parse_trip

MMT_REQUIRED = {"External Id", "Vehicle Registration", "Driver Name", "Driver Phone",
                "Actual End Date", "Actual End Time", "Package", "Total Bill Amount"}
MAX_IMPORT_ROWS = 2000
AIRPORT = "Airport"
AIRPORT_WORDS = re.compile(r"\bairport\b|\bterminal\s*-?\s*[123]\b|\bigi\b|indira gandhi international", re.I)
ZONE_WORDS = {"gurgaon": "Gurgaon", "gurugram": "Gurgaon", "noida": "Noida", "ghaziabad": "Ghaziabad",
              "gaziabad": "Ghaziabad", "delhi": "Delhi"}
# The team's sheet headers, exactly (line breaks and the trailing space included).
SHEET_HEADERS = {
    "total_kms": "Total \nkms", "dead_run_kms": "Dead  \nRun Kms", "extra_km_charges": "Km/Extra/\nKm Charges",
    "extra_time_charges": "Time Extra/\nTime Charges", "night_charge": "Night Charge \n11pm to 5am",
    "cash_to_receive": "Cash to \nbe Recieved", "b2c_payment_status": "B2C payment Status ",
}
HEADER_FILL = PatternFill("solid", fgColor="FFE600")


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


def _pincode_zone(pincode: str) -> str:
    if pincode.startswith("110"):
        return "Delhi"
    if pincode.startswith("122"):
        return "Gurgaon"
    if pincode.startswith("2013"):
        return "Noida"
    return "Ghaziabad" if pincode.startswith("201") else ""


def guess_zone(address: str) -> str:
    """Airport, Delhi, Gurgaon, Noida or Ghaziabad from an address; blank when unsure."""
    address = address or ""
    if AIRPORT_WORDS.search(address):
        return AIRPORT
    pincode = re.search(r"\b\d{6}\b", address)
    if pincode and _pincode_zone(pincode[0]):
        return _pincode_zone(pincode[0])
    found = {zone for word, zone in ZONE_WORDS.items() if word in address.lower()}
    # NCR addresses often mention Delhi as well ("Indirapuram, Ghaziabad, Delhi NCR").
    if len(found) > 1:
        found.discard("Delhi")
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
        # Airport pick-ups are often just "1C / International", so the Package decides that end.
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
    if key == "trip_duration" and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        return time.fromisoformat(value)
    if key == "driver_contact" and value.isdigit():
        return int(value)
    if key in NUMBER_KEYS:
        number = Decimal(value)
        return int(number) if number == number.to_integral_value() else float(number)
    return value


def build_mis_workbook(trips: list[dict]) -> bytes:
    """Same columns, header text and header style as the team's MMT_MIS.xlsx."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append([SHEET_HEADERS.get(key, header) for key, header in MIS_COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(name="Arial", size=10, bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for trip in trips:
        sheet.append([_cell_value(key, trip.get(key, "")) for key, _ in MIS_COLUMNS])
    for index, (key, _) in enumerate(MIS_COLUMNS, 1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = {1: 11, 2: 17}.get(index, 13)
        fmt = ("dd-mm-yyyy" if key in DATE_KEYS else
               "hh:mm" if key in TIME_KEYS or key == "trip_duration" else None)
        if fmt:
            for cell in sheet[letter][1:]:
                cell.number_format = fmt
    sheet.freeze_panes = "A2"
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()
