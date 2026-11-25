"""Load month tabs of the old "Corp Trips Mis" Google Sheet into the MIS.

    python -m scripts.import_mis_sheet <folder> 2026-01:Jan-26 2026-02:Feb-26 ...          (dry run)
    python -m scripts.import_mis_sheet <folder> 2026-01:Jan-26 ... --write                 (writes)
    python -m scripts.import_mis_sheet --remove 2026-01 2026-10                            (undo a load)
    python -m scripts.import_mis_sheet --pipeline <folder>/Pipeline.csv [--write]          (future bookings)

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
from app.services.eee_taxi_mis import MasterIndex, MisTrip, store_trip
from app.services.eee_taxi_mis_sheet import full_plate, header_keys, is_trip_row, sheet_row_to_trip, split_driver_cab
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


def _booked_already(db, trip: MisTrip, taken: set[str]) -> EeeTaxiMisTrip | None:
    """The same booking already in the MIS: same client, day and pick-up time, and the same route or guest.

    ``taken`` holds the trips this run added or already matched: two rows of the tab with the same
    client and time are two cabs, so each needs its own trip.
    """
    rows = db.query(EeeTaxiMisTrip).filter(EeeTaxiMisTrip.company_name == trip.company_name,
                                           EeeTaxiMisTrip.trip_date == date.fromisoformat(trip.date))
    for row in rows:
        if row.id in taken:
            continue
        data = row.data or {}
        same_ride = ((trip.route_no and data.get("route_no") == trip.route_no)
                     or data.get("guest_name", "").lower() == trip.guest_name.lower())
        if data.get("pickup_time", "") == trip.pickup_time and same_ride:
            return row
    return None


def load_pipeline(path: Path, write: bool) -> None:
    """The sheet's Pipeline tab: each row becomes an open MIS trip shown in the Pipeline view."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    keys = header_keys(rows[0])
    with SessionLocal() as db:
        set_added_companies([name for (name,) in db.query(EeeTaxiMisClient.name)])
        index = MasterIndex.build(db)
        added, marked, flagged, days = 0, 0, 0, collections.Counter()
        taken: set[str] = set()
        for line, row in enumerate(rows[1:], 2):
            raw = {key: value for key, value in zip(keys, row) if key}
            if not is_trip_row(raw):
                continue
            driver, cab = split_driver_cab(raw.get("driver_name", ""), raw.get("cab_no", ""))
            raw.update(driver_name=driver, cab_no=full_plate(cab, index.plates))
            try:
                trip = index.fill(sheet_row_to_trip(raw))
            except ValueError as exc:
                sys.stdout.write(f"   row {line} refused: {exc}\n")
                continue
            days[trip.date] += 1
            flagged += bool(index.issues(trip))
            existing = _booked_already(db, trip, taken)
            if existing is not None:
                existing.in_pipeline = MisTrip(**existing.data).status == "Open"
                taken.add(existing.id)
                marked += 1
                continue
            new = store_trip(EeeTaxiMisTrip(source=SOURCE, created_by=EDITOR, in_pipeline=True), trip, EDITOR)
            db.add(new)
            db.flush()
            taken.add(new.id)
            added += 1
        db.commit() if write else db.rollback()
    sys.stdout.write(f"Pipeline: {added} new trips, {marked} already in the MIS (marked for the pipeline), "
                     f"{flagged} with driver or cab still to fill or not in the masters; by day {dict(sorted(days.items()))}\n")
    sys.stdout.write("Written.\n" if write else "Dry run, nothing written.\n")


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--remove":
        remove(argv[1], argv[2])
        return
    if argv and argv[0] == "--pipeline":
        load_pipeline(Path(argv[1]), "--write" in argv)
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
