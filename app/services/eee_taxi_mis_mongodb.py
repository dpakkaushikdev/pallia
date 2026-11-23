"""MongoDB roster report (BA_Raw_Data_Report CSV) -> MIS trips.

The report has one row per guest; guests sharing a Trip ID rode one cab, so
they become one MIS trip. Pick-up is the first boarding, drop is the last
drop-off, and the fare is the costliest billing zone among the guests.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal

from app.services.eee_taxi_mis import MisTrip, format_number, parse_trip
from app.services.eee_taxi_mis_zones import guess_zone
from app.services.mongodb_rates import MongoRateList

COMPANY = "MongoDB"
REQUIRED = {"Trip ID", "Date", "Name", "Direction", "Employee Status", "Planned Pickup Time",
            "Actual Pickup Time", "Actual Drop Time", "Billing Zone", "Registration"}
MAX_GUEST_ROWS = 5000
BLANKS = {"", "-", "--", "na", "n/a", "#n/a"}
# What a later upload of the same report may fill in on a trip that is still open.
FILE_KEYS = (
    "guest_name", "driver_name", "driver_contact", "cab_no", "pickup_address", "pickup_zone", "pickup_time",
    "drop_location", "drop_zone", "drop_time", "trip_end_date", "total_kms", "package", "trip_fare",
)


def _clean(value: str | None) -> str:
    text = " ".join((value or "").split())
    return "" if text.lower() in BLANKS else text


def _moment(value: str) -> datetime | None:
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _first(rows: list[dict[str, str]], *columns: str) -> str:
    return next((row[c] for c in columns for row in rows if row.get(c)), "")


def _phone(text: str) -> str:
    """The first number when the report lists two ('98..., 99...')."""
    return next(iter(re.findall(r"\+?\d[\d -]{8,}\d", text)), text[:20])


def _place(text: str) -> str:
    """'MONGODB_GURGAON' reads better as 'MONGODB GURGAON' and lets the zone be guessed."""
    return text.replace("_", " ")


def _address(rows: list[dict[str, str]], column: str, office: str) -> str:
    """First real address in ``column``; the report sometimes repeats an office name there."""
    prefix = office.split()[0].upper() if office else None
    return next((row[column] for row in rows
                 if row.get(column) and not (prefix and row[column].upper().startswith(prefix))), "")


def _fare(guests: list[dict[str, str]], rates: MongoRateList) -> tuple[str, Decimal | None, list[str]]:
    """Costliest zone among the guests, its fare, and any zone the rate list does not have."""
    zones = list(dict.fromkeys(g["Billing Zone"] for g in guests if g.get("Billing Zone")))
    priced = [(rates.fare_for(zone), zone) for zone in zones]
    unknown = [zone for fare, zone in priced if fare is None]
    known = [(fare, zone) for fare, zone in priced if fare is not None]
    if unknown or not known:
        return (zones[0] if zones else ""), None, unknown
    fare, zone = max(known, key=lambda pair: pair[0])
    return zone, fare, []


def group_to_trip(trip_id: str, guests: list[dict[str, str]], dispatcher: str,
                  rates: MongoRateList) -> tuple[dict[str, str], str]:
    """One MIS trip (as a dict of MIS keys) for the guests of one Trip ID, plus a warning or ''."""
    boarded = [g for g in guests if g.get("Employee Status", "").lower() == "boarded"]
    planned = sorted(guests, key=lambda g: g.get("Planned Pickup Time", ""))
    pickups = sorted(m for m in (_moment(g.get("Actual Pickup Time", "")) for g in boarded) if m)
    drops = sorted(m for m in (_moment(g.get("Actual Drop Time", "")) for g in boarded) if m)
    start = pickups[0] if pickups else _moment(planned[0].get("Planned Pickup Time", ""))
    end = drops[-1] if drops else None

    office = _place(_first(guests, "Office", "Trip Office"))
    office_zone = guess_zone(office)
    zone, fare, unknown = _fare(guests, rates)
    logout = _first(guests, "Direction").upper() == "LOGOUT"
    if logout:
        last_out = sorted(guests, key=lambda g: g.get("Actual Drop Time") or g.get("Planned Drop Time", ""), reverse=True)
        route = {"pickup_address": office, "pickup_zone": office_zone,
                 "drop_location": _address(last_out, "Selected Destination", office), "drop_zone": zone}
    else:
        first_in = sorted(boarded, key=lambda g: g.get("Actual Pickup Time", "")) + planned
        route = {"pickup_address": _address(first_in, "Nodal Point", office), "pickup_zone": zone,
                 "drop_location": office, "drop_zone": office_zone}

    kms = max((Decimal(g["Distance Travelled(KM)"]) for g in boarded
               if g.get("Distance Travelled(KM)", "").replace(".", "", 1).isdigit()), default=Decimal(0))
    missed = [g["Name"] for g in guests if g not in boarded and g.get("Name")] if boarded else []
    remarks = " ".join(filter(None, [
        _first(guests, "Direction").title(), _first(guests, "Shift Type/Time"),
        ("- Not boarded: " + ", ".join(missed)) if missed else "",
    ]))
    warning = ""
    if boarded and unknown:
        warning = f"Trip {trip_id}: zone '{unknown[0]}' is not in the MongoDB rate list, so no fare was filled."
    elif boarded and fare is None:
        warning = f"Trip {trip_id}: the report gives no Billing Zone, so no fare was filled."
    billable = bool(boarded) and fare is not None
    return {
        "date": start.date().isoformat() if start else _first(guests, "Date"),
        "dispatcher_name": dispatcher,
        "driver_name": _first(guests, "Actual Driver Name", "Planned Driver Name"),
        "driver_contact": _phone(_first(guests, "Actual Driver Contact No", "Planned Driver Contact No")),
        "cab_no": _first(guests, "Registration"),
        "route_no": trip_id,
        "company_name": COMPANY,
        "guest_name": "/".join(dict.fromkeys(g["Name"] for g in planned if g.get("Name")))[:500],
        **route,
        "pickup_time": start.strftime("%H:%M") if start else "",
        "drop_time": end.strftime("%H:%M") if end else "",
        "trip_end_date": end.date().isoformat() if end else "",
        "total_kms": format_number(str(kms)) if kms > 0 else "",
        "package": format_number(str(fare)) if billable else "",
        "trip_fare": format_number(str(fare)) if billable else "",
        "remarks": remarks[:500],
    }, warning


def parse_mongodb_report(data: bytes, dispatcher: str, rates: MongoRateList) -> tuple[list[MisTrip], list[str]]:
    """Trips in file order, plus one message per trip that could not be read or priced."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = {(h or "").strip() for h in reader.fieldnames or []}
    if not REQUIRED <= headers:
        raise ValueError("This is not a MongoDB raw data report (.csv): columns such as Trip ID, "
                         "Billing Zone and Actual Pickup Time are missing.")
    groups: dict[str, list[dict[str, str]]] = {}
    for count, raw in enumerate(reader, 1):
        if count > MAX_GUEST_ROWS:
            raise ValueError(f"Upload at most {MAX_GUEST_ROWS} guest rows at a time.")
        row = {(key or "").strip(): _clean(value) for key, value in raw.items() if isinstance(value, str)}
        if row.get("Trip ID"):
            groups.setdefault(row["Trip ID"], []).append(row)
    trips, messages = [], []
    for trip_id, guests in groups.items():
        try:
            values, warning = group_to_trip(trip_id, guests, dispatcher, rates)
            trips.append(parse_trip(values))
        except ValueError as exc:
            messages.append(f"Trip {trip_id}: {exc}")
            continue
        if warning:
            messages.append(warning)
    return trips, messages


def merge_file_values(existing: dict, fresh: MisTrip) -> dict:
    """An open trip refreshed from a later report: file columns are replaced, the rest is kept."""
    update = {key: getattr(fresh, key) for key in FILE_KEYS if getattr(fresh, key)}
    # Derived values are worked out again from the new times and fare.
    cleared = {"trip_duration": "", "total_trip_fare": ""}
    return {**existing, **cleared, **update, "date": fresh.date}
