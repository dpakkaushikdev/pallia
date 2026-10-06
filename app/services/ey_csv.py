"""EY CSV validation; PWC parsing and client validation remain the defaults."""
import csv
import io
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from app.services.eee_taxi_csv import _normalize_header, parse_eee_taxi_csv
from app.services.ey_clients import EY_CLIENTS
from app.services.ey_rates import EyRateCard


def duration_minutes(value: str) -> int:
    if not re.fullmatch(r"\d{1,3}:[0-5]\d(?::00)?", value.strip()):
        raise ValueError("Trip Duration must be H:MM (for example 1:00 for 60 minutes).")
    hours, minutes = value.strip().split(":")[:2]
    return int(hours) * 60 + int(minutes)


def clock_minutes(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::[0-5]\d)?\s*(AM|PM)?", value.strip(), re.I)
    if not match:
        raise ValueError("Pickup and drop times must be HH:MM or h:mm AM/PM.")
    h, m, ampm = int(match[1]), int(match[2]), (match[3] or "").upper()
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError("AM/PM hours must be between 1 and 12.")
        h = h % 12 + (12 if ampm == "PM" else 0)
    elif h > 23:
        raise ValueError("24-hour times must be between 00:00 and 23:59.")
    return h * 60 + m


def parse_ey_csv(content: bytes, rates: EyRateCard):
    records = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    header_index = next((i for i, cells in enumerate(records) if any(c.strip() for c in cells)), None)
    if header_index is None:
        raise ValueError("The EY CSV is empty.")
    headers = records[header_index]
    columns = {_normalize_header(h): i for i, h in enumerate(headers)}
    if "eng code" not in columns:
        raise ValueError('EY CSV needs the "Eng Code" column for Buyer\'s Order No.')
    for key in ("eng code", "total kms", "package", "trip duration", "pick up time", "drop time"):
        if sum(_normalize_header(h) == key for h in headers) > 1:
            raise ValueError(f"EY CSV has duplicate {key!r} columns.")
    for number, cells in enumerate(records[header_index + 1:], header_index + 2):
        if not any(c.strip() for c in cells) or cells[0].strip().lower() in ("grand total", "total", ""):
            continue
        def cell(name):
            i = columns.get(name)
            return cells[i].strip() if i is not None and i < len(cells) else ""
        try:
            if not cell("eng code"):
                raise ValueError("Eng Code is blank.")
            duration_minutes(cell("trip duration"))
            clock_minutes(cell("pick up time"))
            clock_minutes(cell("drop time"))
            for name in ("total kms", "mcd", "parking", "toll", "kms helper"):
                value = cell(name)
                if not value and name != "total kms":
                    continue
                for part in value.split("+"):
                    amount = Decimal(part.replace(",", "").strip())
                    if not amount.is_finite() or amount < 0:
                        raise ValueError(f"{name} must contain non-negative numbers.")
            package = cell("package")
            if package.upper() not in ("", "P2P", "POINT TO POINT"):
                amount = Decimal(package.replace(",", ""))
                if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
                    raise ValueError("Package must be a whole-number fare or P2P.")
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"EY CSV row {number}: {exc or 'invalid numeric value'}") from exc
    original, rows = parse_eee_taxi_csv(content, rates, client_master=EY_CLIENTS)
    return original, [replace(row, client_profile="ey", total_amount=Decimal("0")) for row in rows]
