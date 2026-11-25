"""Rows of the team's old "Corp Trips Mis" month sheets -> MIS trips.

The sheets were typed by hand over a year, so this reader is forgiving: a cell
the MIS cannot hold (text in a money column, a negative km, "1840" for a time)
is left empty and what the sheet said goes into Remarks; a client that is not
in the client list is filed under Others with its name kept. Only a row with
no usable date is refused.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import ValidationError

from app.services.eee_taxi_mis import COLUMN_LABELS, MIS_COLUMNS, NUMBER_KEYS, TIME_KEYS, MisTrip
from app.services.eee_taxi_mis_zones import OTHERS, canonical_company

MAX_FIXES = 12
MAX_END_GAP_DAYS = 3
ODOMETER_KEYS = ("start_odometer", "end_odometer")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Sheet header (letters and digits only) -> MIS key; the older tabs named a few columns differently.
HEADER_KEYS = {
    **{_key(label): key for key, label in MIS_COLUMNS},
    "carno": "cab_no", "pickuplocation": "pickup_address", "tollmcd": "mcd",
}


def header_keys(headers: list[str]) -> list[str | None]:
    return [HEADER_KEYS.get(_key(header)) for header in headers]


def _clock(value: str) -> str:
    """'4:21 AM' and '1840' as HH:MM; anything else is returned as typed."""
    for pattern in ("%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(value.upper(), pattern).strftime("%H:%M")
        except ValueError:
            continue
    digits = re.fullmatch(r"([01]\d|2[0-3])([0-5]\d)", value)
    return f"{digits[1]}:{digits[2]}" if digits else value


def _dates(value: str) -> list[date]:
    """What a slash date can mean: day first, then month first (some tabs mixed the two)."""
    found = []
    for pattern in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            found.append(datetime.strptime(value, pattern).date())
        except ValueError:
            continue
    return found


def _trip_date(value: str, month: tuple[int, int] | None) -> str:
    """The reading that falls in the tab's month, else day first."""
    options = _dates(value)
    in_month = [d for d in options if month and (d.year, d.month) == month]
    return (in_month or options or [None])[0].isoformat() if options else value


def _end_date(value: str, start: str) -> str:
    """The reading within a few days after the trip date; a trip does not end weeks later."""
    try:
        begin = date.fromisoformat(start)
    except ValueError:
        return value
    near = [d for d in _dates(value) if 0 <= (d - begin).days <= MAX_END_GAP_DAYS]
    return near[0].isoformat() if near else value


def _number(value: str) -> str:
    return value.replace("₹", "").replace("`", "").replace(",", "").strip()


def _note(notes: list[str], key: str, value: str) -> None:
    notes.append(f"{COLUMN_LABELS.get(key, key)} in sheet: {value}")


def sheet_row_to_trip(raw: dict[str, str], month: tuple[int, int] | None = None) -> MisTrip:
    """One sheet row (MIS key -> cell text) as a trip. Raises ValueError only when the date is unusable."""
    values = {key: " ".join((value or "").split()) for key, value in raw.items() if key in COLUMN_LABELS}
    notes: list[str] = []
    values["date"] = _trip_date(values.get("date", ""), month)
    if values.get("trip_end_date"):
        values["trip_end_date"] = _end_date(values["trip_end_date"], values["date"])
    for key in TIME_KEYS:
        values[key] = _clock(values.get(key, ""))
    for key in NUMBER_KEYS:
        values[key] = _number(values.get(key, ""))
    typed = values.get("company_name", "")
    if canonical_company(typed) is None:
        values["company_name"], values["company_other"] = OTHERS, typed[:120] or "Not named in sheet"

    for _ in range(MAX_FIXES):
        try:
            trip = MisTrip(**values)
            break
        except ValidationError as exc:
            for error in exc.errors():
                key = error["loc"][0] if error["loc"] else ""
                if key == "date":
                    raise ValueError(f"Date '{values.get('date', '')}' cannot be read.") from None
                if error["type"] == "string_too_long":
                    values[key] = values[key][:error["ctx"]["max_length"]]
                elif key:
                    _note(notes, key, values[key])
                    values[key] = ""
                else:   # the only rule across columns: End Odometer below Start Odometer
                    for odometer in ODOMETER_KEYS:
                        _note(notes, odometer, values.get(odometer, ""))
                        values[odometer] = ""
    else:
        raise ValueError("Row could not be read.")
    if not notes:
        return trip
    remarks = " | ".join(filter(None, [trip.remarks, *notes]))[:500]
    return trip.model_copy(update={"remarks": remarks})


def split_driver_cab(driver: str, cab: str) -> tuple[str, str]:
    """The Pipeline tab sometimes types the cab's last four digits after the driver ('Ravi 5215')."""
    tail = re.fullmatch(r"(.*?\D)\s*(\d{4})", driver.strip())
    return (tail[1].strip(), tail[2]) if tail and not cab.strip() else (driver.strip(), cab.strip())


def full_plate(cab: str, plates: set[str]) -> str:
    """A cab typed as its last four digits, as the full number when only one cab in the master ends so."""
    short = re.sub(r"[^A-Z0-9]", "", cab.upper())
    if not re.fullmatch(r"\d{4}", short):
        return cab
    matches = [plate for plate in plates if plate.endswith(short)]
    return matches[0] if len(matches) == 1 else cab


def is_trip_row(raw: dict[str, str]) -> bool:
    """Month tabs carry formula-only rows below the data; a trip has at least one of these."""
    return any((raw.get(key) or "").strip() for key in ("date", "company_name", "guest_name", "cab_no", "driver_name"))
