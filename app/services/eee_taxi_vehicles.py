"""Fleet CSV master and vehicle labels shared by invoice PDFs and Tally XML."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiVehicleMaster
from app.services.eee_taxi_cost_centres import normalize_vehicle_no

CSV_FIELDS = {
    "vehicle_no": "Vehicle Number Plate", "availability": "Availability",
    "pollution_expiry": "Pollution Expiry", "make": "Make", "registration_date": "Regn Date",
    "chassis_no": "Chassis no.", "engine_no": "Engine no.",
}
DEFAULT_CSV = Path(__file__).resolve().parents[1] / "templates" / "vehicle_master.csv"


class VehicleRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    vehicle_no: str = Field(min_length=1, max_length=20)
    make: str = Field(min_length=1, max_length=100)
    availability: str = Field(default="", max_length=100)
    pollution_expiry: str = Field(default="", max_length=40)
    registration_date: str = Field(default="", max_length=40)
    chassis_no: str = Field(default="", max_length=100)
    engine_no: str = Field(default="", max_length=100)

    @field_validator("vehicle_no")
    @classmethod
    def valid_plate(cls, value):
        value = normalize_vehicle_no(value)
        if not re.fullmatch(r"[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}", value):
            raise ValueError("Enter a valid vehicle registration number.")
        return value


class VehicleRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[VehicleRow] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_plates(self):
        seen = set()
        for row in self.rows:
            if row.vehicle_no in seen:
                raise ValueError(f"Vehicle {row.vehicle_no} appears more than once.")
            seen.add(row.vehicle_no)
        return self


def parse_vehicle_csv(data: bytes) -> list[VehicleRow]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if not reader.fieldnames or not {"Vehicle Number Plate", "Make"} <= set(reader.fieldnames):
            raise ValueError("CSV must contain Vehicle Number Plate and Make columns.")
        rows = []
        for line, raw in enumerate(reader, 2):
            if not any(raw.values()):
                continue
            if len(rows) >= 2000:
                raise ValueError("Vehicle master cannot exceed 2000 vehicles.")
            try:
                rows.append(VehicleRow(**{key: raw.get(column) or "" for key, column in CSV_FIELDS.items()}))
            except ValueError as exc:
                raise ValueError(f"CSV row {line}: {exc}") from exc
        return VehicleRows(rows=rows).rows
    except UnicodeError as exc:
        raise ValueError("Save the vehicle master as a UTF-8 CSV file.") from exc


def get_vehicle_rows(db: Session) -> list[VehicleRow]:
    stored = db.get(EeeTaxiVehicleMaster, 1)
    if stored is not None:
        rows = VehicleRows(rows=stored.rows).rows
        # Upgrade copies saved before the fleet make correction as well as
        # the bundled CSV, without replacing any other vehicle details.
        for row in rows:
            if row.make == "TIGOR- XR":
                row.make = "TIGOR-EV"
        return rows
    return parse_vehicle_csv(DEFAULT_CSV.read_bytes())


def vehicle_make_map(db: Session | None = None) -> dict[str, str]:
    rows = get_vehicle_rows(db) if db is not None else parse_vehicle_csv(DEFAULT_CSV.read_bytes())
    return {row.vehicle_no: row.make for row in rows}


def vehicle_display_name(vehicle_no: str, master: dict[str, str] | None = None) -> str:
    master = vehicle_make_map() if master is None else master
    plate = normalize_vehicle_no(vehicle_no)
    make = master.get(plate, "")
    return f"{plate}({make})" if make else vehicle_no.strip()


def save_vehicle_rows(db: Session, rows: list[VehicleRow], editor: str) -> None:
    stored = db.get(EeeTaxiVehicleMaster, 1)
    if stored is None:
        stored = EeeTaxiVehicleMaster(id=1)
        db.add(stored)
    stored.rows = [row.model_dump() for row in rows]
    stored.updated_by = editor
    stored.updated_at = datetime.utcnow()
    db.commit()
