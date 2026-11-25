"""Load month tabs of the old "Corp Trips Mis" Google Sheet into the MIS.

    python -m scripts.import_mis_sheet <folder> 2026-01:Jan-26 2026-02:Feb-26 ...          (dry run)
    python -m scripts.import_mis_sheet <folder> 2026-01:Jan-26 ... --write                 (writes)
    python -m scripts.import_mis_sheet --remove 2026-01 2026-10                            (undo a load)

Each ``YYYY-MM:name`` pair is one tab saved as ``<folder>/<name>.csv`` (File > Download > CSV).
Loaded trips get source "sheet"; a month that already has sheet trips is refused, so a
second run cannot double them. Writes go to whatever database ``.env`` points at.
"""
from __future__ import annotations

import collections
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, insert

from app.database import SessionLocal
from app.models import EeeTaxiMisClient, EeeTaxiMisTrip
from app.services.eee_taxi_mis import MisTrip
from app.services.eee_taxi_mis_sheet import header_keys, is_trip_row, sheet_row_to_trip
from app.services.eee_taxi_mis_zones import set_added_companies

SOURCE = "sheet"
EDITOR = "sheet-import"
CHUNK = 500


def read_tab(path: Path, month: tuple[int, int]) -> tuple[list[MisTrip], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    keys = header_keys(rows[0])
    trips, refused = [], []
    for line, row in enumerate(rows[1:], 2):
        raw = {key: value for key, value in zip(keys, row) if key}
        if not is_trip_row(raw):
            continue
        try:
            trips.append(sheet_row_to_trip(raw, month))
        except ValueError as exc:
            refused.append(f"{path.stem} row {line}: {exc}")
    return trips, refused


def _month_range(month: tuple[int, int]) -> tuple[date, date]:
    year, number = month
    return date(year, number, 1), date(year + number // 12, number % 12 + 1, 1)


def _record(trip: MisTrip, now: datetime) -> dict:
    return {"company_name": trip.company_name, "route_no": trip.route_no or None,
            "trip_date": date.fromisoformat(trip.date), "data": trip.model_dump(), "source": SOURCE,
            "in_pipeline": False, "created_by": EDITOR, "created_at": now, "updated_by": EDITOR, "updated_at": now}


def remove(first: str, last: str) -> None:
    start = date.fromisoformat(first + "-01")
    end = _month_range(tuple(int(part) for part in last.split("-")))[1]
    with SessionLocal() as db:
        deleted = db.query(EeeTaxiMisTrip).filter(
            EeeTaxiMisTrip.source == SOURCE, EeeTaxiMisTrip.trip_date >= start,
            EeeTaxiMisTrip.trip_date < end).delete(synchronize_session=False)
        db.commit()
    sys.stdout.write(f"Removed {deleted} sheet trips dated {start} to before {end}.\n")


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--remove":
        remove(argv[1], argv[2])
        return
    write = "--write" in argv
    folder, *pairs = [arg for arg in argv if arg != "--write"]
    with SessionLocal() as db:
        set_added_companies([name for (name,) in db.query(EeeTaxiMisClient.name)])
        total = 0
        for pair in pairs:
            label, name = pair.split(":", 1)
            month = tuple(int(part) for part in label.split("-"))
            trips, refused = read_tab(Path(folder) / f"{name}.csv", month)
            start, end = _month_range(month)
            outside = sum(1 for trip in trips if not start <= date.fromisoformat(trip.date) < end)
            status = collections.Counter(trip.status for trip in trips)
            others = sum(1 for trip in trips if trip.company_other)
            noted = sum(1 for trip in trips if " in sheet: " in trip.remarks)
            sys.stdout.write(f"{name}: {len(trips)} trips ({status['Closed']} closed, {status['Open']} open), "
                             f"{others} under Others, {noted} with a blanked cell, {outside} dated outside "
                             f"{label}, {len(refused)} refused\n")
            for message in refused[:5]:
                sys.stdout.write(f"   {message}\n")
            if not write:
                continue
            already = db.query(func.count(EeeTaxiMisTrip.id)).filter(
                EeeTaxiMisTrip.source == SOURCE, EeeTaxiMisTrip.trip_date >= start,
                EeeTaxiMisTrip.trip_date < end).scalar()
            if already:
                sys.stdout.write(f"   SKIPPED: {already} sheet trips of {label} are already loaded.\n")
                continue
            now = datetime.utcnow()
            records = [_record(trip, now) for trip in trips]
            for at in range(0, len(records), CHUNK):
                db.execute(insert(EeeTaxiMisTrip), records[at:at + CHUNK])
            db.commit()
            total += len(records)
            sys.stdout.write(f"   written: {len(records)}\n")
    sys.stdout.write(f"{'Wrote' if write else 'Dry run, nothing written. Would write'} {total if write else 'the trips above'}.\n")


if __name__ == "__main__":
    main(sys.argv[1:])
