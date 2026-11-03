"""One-time load of Master.xlsx (Driver Master + Vehicle Master) into the EEE-Taxi masters.

The Vehicle Master is replaced by the sheet. Only useful driver details are kept;
Aadhaar, bank and image columns are ignored. After this, drivers and vehicles are
edited one row at a time on the Masters page.

    .venv\\Scripts\\python.exe -m scripts.import_eee_taxi_master Master.xlsx            # preview
    .venv\\Scripts\\python.exe -m scripts.import_eee_taxi_master Master.xlsx --apply    # write to DB
"""
from __future__ import annotations

import argparse
from datetime import datetime

from openpyxl import load_workbook

from app.services.eee_taxi_drivers import DriverRow, DriverRows
from app.services.eee_taxi_vehicles import VehicleRow, VehicleRows

DRIVER_COLUMNS = {
    "driver_id": "Driver ID", "name": "Name", "truein_id": "Truein ID", "mobile": "Mobile",
    "alternate_mobile": "Alternate Mobile", "father_name": "Father Name", "email": "Email ID",
    "status": "Status", "availability": "Availability Status", "branch": "Branch", "department": "Department",
    "date_of_joining": "Date of Joining", "dl_expiry": "Driver License Expiry Date",
    "emergency_contact_name": "Emergency Contact Name", "emergency_contact_number": "Emergency Contact Number",
    "emergency_contact_relationship": "Emergency Contact Relationship",
}
DRIVER_DATES = {"date_of_joining", "dl_expiry"}
VEHICLE_COLUMNS = {
    "vehicle_no": "Vehicle Number Plate", "make": "Make", "availability": "Availability",
    "pollution_expiry": "Pollution Expiry", "registration_date": "Regn Date",
    "chassis_no": "Chassis no.", "engine_no": "Engine no.",
}
VEHICLE_DATES = {"pollution_expiry", "registration_date"}
_BLANKS = {"", "NA", "N/A", "-", "NONE"}


def _text(value, is_date: bool) -> str:
    if isinstance(value, datetime):
        # 1970-01-01 is the fleet export's placeholder for "no date".
        return value.strftime("%d/%m/%Y") if is_date and value.year > 1970 else ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value if value is not None else "").split())
    return "" if text.upper() in _BLANKS else text


def _sheet_rows(book, sheet: str, columns: dict[str, str], dates: set[str]) -> list[dict]:
    rows = book[sheet].iter_rows(values_only=True)
    headers = [" ".join(str(h or "").split()) for h in next(rows)]
    missing = [title for title in columns.values() if title not in headers]
    if missing:
        raise ValueError(f"{sheet} is missing columns: {', '.join(missing)}")
    index = {key: headers.index(title) for key, title in columns.items()}
    return [{key: _text(raw[i], key in dates) for key, i in index.items()}
            for raw in rows if any(v not in (None, "") for v in raw)]


def read_master_workbook(source) -> tuple[list[VehicleRow], list[DriverRow]]:
    book = load_workbook(source, data_only=True, read_only=True)
    try:
        vehicles = VehicleRows(rows=_sheet_rows(book, "Vehicle Master", VEHICLE_COLUMNS, VEHICLE_DATES)).rows
        drivers = DriverRows(rows=_sheet_rows(book, "Driver Master", DRIVER_COLUMNS, DRIVER_DATES)).rows
    finally:
        book.close()
    return vehicles, drivers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook")
    parser.add_argument("--apply", action="store_true", help="write to the database in .env")
    args = parser.parse_args()
    vehicles, drivers = read_master_workbook(args.workbook)
    print(f"Read {len(vehicles)} vehicles and {len(drivers)} drivers.")
    if not args.apply:
        print("Preview only. Re-run with --apply to replace both masters.")
        return
    from app.database import SessionLocal, init_db
    from app.services.eee_taxi_drivers import save_driver_rows
    from app.services.eee_taxi_vehicles import save_vehicle_rows
    init_db()
    with SessionLocal() as db:
        save_vehicle_rows(db, vehicles, "Master.xlsx import")
        save_driver_rows(db, drivers, "Master.xlsx import")
    print("Saved both masters.")


if __name__ == "__main__":
    main()
