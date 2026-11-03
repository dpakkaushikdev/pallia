"""EEE-Taxi MIS: one row per corporate trip, entered by hand or uploaded.

Column order and headers follow the team's MMT_MIS.xlsx sheet. Every value is
kept as text (dates as YYYY-MM-DD, times as HH:MM) so a half-filled trip can
still be saved; drivers and cabs are checked against the masters and any
mismatch is reported with the trip instead of blocking the save.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiMisTrip
from app.services.eee_taxi_cost_centres import normalize_vehicle_no
from app.services.eee_taxi_drivers import DriverRow, get_driver_rows
from app.services.eee_taxi_vehicles import get_vehicle_rows

# (key, Excel header) in MMT_MIS.xlsx order.
MIS_COLUMNS = [
    ("date", "Date"), ("dispatcher_name", "Dispatcher Name"), ("driver_id", "Driver ID"),
    ("driver_name", "Driver Name"), ("driver_contact", "Driver Cont No"), ("cab_no", "Cab No"),
    ("route_no", "DS no/Route No"), ("company_name", "Company Name"), ("guest_name", "Guest Name"),
    ("pickup_address", "Pickup Address"), ("pickup_zone", "Pickup Zone"), ("pickup_time", "Pick up Time"),
    ("drop_location", "Drop Location"), ("drop_zone", "Drop Zone"), ("drop_time", "Drop Time"),
    ("trip_end_date", "Trip End Date"), ("trip_duration", "Trip Duration"), ("hub_out", "Hub Out"),
    ("hub_in", "Hub In"), ("start_odometer", "Start Odometer"), ("end_odometer", "End Odometer"),
    ("total_kms", "Total kms"), ("dead_run_kms", "Dead Run Kms"), ("package", "Package"),
    ("extra_km_charges", "Km/Extra/Km Charges"), ("extra_time_charges", "Time Extra/Time Charges"),
    ("night_charge", "Night Charge 11pm to 5am"), ("trip_fare", "Trip Fare"), ("mcd", "MCD"),
    ("parking", "Parking"), ("toll", "Toll"), ("driver_hours", "Driver Hours"),
    ("cash_to_receive", "Cash to be Recieved"), ("remarks", "Remarks"), ("mmt_base_cost", "MMT Base Cost"),
    ("mmt_prepaid_amount", "MMT Peripaid Amt"), ("duty_closed_by", "Duty closed by"),
    ("b2c_payment_status", "B2C payment Status"),
]
DATE_KEYS = ("date", "trip_end_date")
TIME_KEYS = ("pickup_time", "drop_time", "hub_out", "hub_in")
NUMBER_KEYS = (
    "start_odometer", "end_odometer", "total_kms", "dead_run_kms", "extra_km_charges",
    "extra_time_charges", "night_charge", "trip_fare", "mcd", "parking", "toll",
    "cash_to_receive", "mmt_base_cost", "mmt_prepaid_amount",
)
COMPANIES = ("MMT", "EY", "PWC")
MAX_LIST_ROWS = 3000

Short = Annotated[str, Field(default="", max_length=120)]
Long = Annotated[str, Field(default="", max_length=500)]


class MisTrip(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    date: str = Field(min_length=1, max_length=20)
    dispatcher_name: Short
    driver_id: Short
    driver_name: Short
    driver_contact: str = Field(default="", max_length=20)
    cab_no: str = Field(default="", max_length=20)
    route_no: str = Field(default="", max_length=64)
    company_name: str = Field(min_length=1, max_length=32)
    guest_name: Short
    pickup_address: Long
    pickup_zone: Short
    pickup_time: str = ""
    drop_location: Long
    drop_zone: Short
    drop_time: str = ""
    trip_end_date: str = ""
    trip_duration: str = Field(default="", max_length=20)
    hub_out: str = ""
    hub_in: str = ""
    start_odometer: str = ""
    end_odometer: str = ""
    total_kms: str = ""
    dead_run_kms: str = ""
    package: Short
    extra_km_charges: str = ""
    extra_time_charges: str = ""
    night_charge: str = ""
    trip_fare: str = ""
    mcd: str = ""
    parking: str = ""
    toll: str = ""
    driver_hours: str = Field(default="", max_length=20)
    cash_to_receive: str = ""
    remarks: Long
    mmt_base_cost: str = ""
    mmt_prepaid_amount: str = ""
    duty_closed_by: Short
    b2c_payment_status: Short

    @field_validator(*DATE_KEYS)
    @classmethod
    def iso_date(cls, value: str) -> str:
        return format_date(value)

    @field_validator(*TIME_KEYS)
    @classmethod
    def hh_mm(cls, value: str) -> str:
        return format_time(value)

    @field_validator(*NUMBER_KEYS)
    @classmethod
    def number(cls, value: str) -> str:
        return format_number(value)

    @field_validator("cab_no")
    @classmethod
    def plate(cls, value: str) -> str:
        return normalize_vehicle_no(value)

    @field_validator("driver_contact")
    @classmethod
    def phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("company_name")
    @classmethod
    def company(cls, value: str) -> str:
        return value.upper() if value.upper() in COMPANIES else value

    @model_validator(mode="after")
    def derived_values(self):
        duration = trip_duration(self.date, self.pickup_time, self.trip_end_date, self.drop_time)
        if duration:
            self.trip_duration = duration
        if self.start_odometer and self.end_odometer:
            kms = Decimal(self.end_odometer) - Decimal(self.start_odometer)
            if kms < 0:
                raise ValueError("End Odometer is lower than Start Odometer.")
            self.total_kms = format_number(str(kms))
        return self


COLUMN_LABELS = dict(MIS_COLUMNS)


def parse_trip(data: dict) -> MisTrip:
    """Validate one trip, naming the MIS column in every error message."""
    try:
        return MisTrip(**data)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            column = COLUMN_LABELS.get(error["loc"][0] if error["loc"] else "")
            text = error["msg"].removeprefix("Value error, ")
            messages.append(f"{column}: {text}" if column else text)
        raise ValueError("; ".join(messages)) from None


def format_date(value: str) -> str:
    value = (value or "").strip()
    if value in ("", "-"):
        return ""
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a date (use DD/MM/YYYY).")


def format_time(value: str) -> str:
    value = (value or "").strip()
    if value in ("", "-"):
        return ""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", value)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"'{value}' is not a time (use HH:MM).")
    return f"{int(match[1]):02d}:{match[2]}"


def format_number(value: str) -> str:
    value = (value or "").strip().replace(",", "")
    if value in ("", "-"):
        return ""
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a number.") from None
    if not number.is_finite() or number < 0 or number >= 10 ** 9:
        raise ValueError(f"'{value}' is not a valid amount.")
    number = number.quantize(Decimal("0.01")).normalize()
    return format(number, "f")


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


MIN_NAME_PREFIX = 4


def name_key(name: str) -> str:
    """'Umakant  Dwivedi.' and 'UMAKANTDWIVEDI' compare equal."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def trip_duration(start_date: str, start_time: str, end_date: str, end_time: str) -> str:
    """'HH:MM' from pick-up to drop; a drop before pick-up with no end date means next day."""
    if not (start_date and start_time and end_time):
        return ""
    start = datetime.fromisoformat(f"{start_date}T{start_time}")
    end = datetime.fromisoformat(f"{end_date or start_date}T{end_time}")
    if end < start and not end_date:
        end += timedelta(days=1)
    if end < start:
        return ""
    minutes = int((end - start).total_seconds() // 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class MasterIndex:
    """Driver and cab lookups used to fill and check trips."""
    by_truein: dict[str, DriverRow] = field(default_factory=dict)
    by_phone: dict[str, DriverRow] = field(default_factory=dict)
    by_name: dict[str, DriverRow] = field(default_factory=dict)
    all_names: list[tuple[str, DriverRow]] = field(default_factory=list)
    plates: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, db: Session) -> "MasterIndex":
        index = cls(plates={row.vehicle_no for row in get_vehicle_rows(db)})
        names: dict[str, list[DriverRow]] = {}
        for driver in get_driver_rows(db):
            if driver.truein_id:
                index.by_truein[driver.truein_id.upper()] = driver
            for phone in (driver.mobile, driver.alternate_mobile):
                if normalize_phone(phone):
                    index.by_phone.setdefault(normalize_phone(phone), driver)
            names.setdefault(name_key(driver.name), []).append(driver)
        # A name shared by two drivers cannot identify either of them.
        index.by_name = {name: rows[0] for name, rows in names.items() if len(rows) == 1}
        index.all_names = [(name, driver) for name, rows in names.items() for driver in rows]
        return index

    def find_by_name(self, name: str) -> DriverRow | None:
        """Same name ignoring spaces, case and dots; else the only master name starting with it."""
        key = name_key(name)
        if not key:
            return None
        if key in self.by_name:
            return self.by_name[key]
        if len(key) < MIN_NAME_PREFIX:
            return None
        starts = [driver for other, driver in self.all_names if other.startswith(key)]
        return starts[0] if len(starts) == 1 else None

    def find_driver(self, trip: MisTrip) -> DriverRow | None:
        return (self.by_truein.get(trip.driver_id.upper())
                or self.by_phone.get(trip.driver_contact)
                or self.find_by_name(trip.driver_name))

    def fill(self, trip: MisTrip) -> MisTrip:
        """Copy the matched driver's Truein ID, master name and mobile onto the trip."""
        driver = self.find_driver(trip)
        if driver is None:
            return trip
        return trip.model_copy(update={
            "driver_id": driver.truein_id or trip.driver_id,
            "driver_name": driver.name,
            "driver_contact": normalize_phone(driver.mobile) or trip.driver_contact,
        })

    def issues(self, trip: MisTrip) -> list[str]:
        problems = []
        driver = self.find_driver(trip)
        if not (trip.driver_id or trip.driver_name or trip.driver_contact):
            problems.append("Driver missing")
        elif driver is None:
            problems.append("Driver not in driver master")
        elif driver.status == "Deactive":
            problems.append("Driver is Deactive in the master")
        if not trip.cab_no:
            problems.append("Cab No missing")
        elif trip.cab_no not in self.plates:
            problems.append(f"Cab {trip.cab_no} not in vehicle master")
        return problems


def trip_response(row: EeeTaxiMisTrip, index: MasterIndex) -> dict:
    trip = MisTrip(**row.data)
    return {
        **trip.model_dump(), "id": row.id, "source": row.source,
        "created_by": row.created_by, "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() + "Z", "issues": index.issues(trip),
    }


def find_route_conflict(db: Session, trip: MisTrip, exclude_id: str | None = None) -> EeeTaxiMisTrip | None:
    if not trip.route_no:
        return None
    query = db.query(EeeTaxiMisTrip).filter(
        EeeTaxiMisTrip.company_name == trip.company_name, EeeTaxiMisTrip.route_no == trip.route_no)
    if exclude_id:
        query = query.filter(EeeTaxiMisTrip.id != exclude_id)
    return query.first()


def store_trip(row: EeeTaxiMisTrip, trip: MisTrip, editor: str) -> EeeTaxiMisTrip:
    row.data = trip.model_dump()
    row.company_name = trip.company_name
    row.route_no = trip.route_no or None
    row.trip_date = date.fromisoformat(trip.date)
    row.updated_by = editor
    row.updated_at = datetime.utcnow()
    return row


def list_trips(db: Session, date_from: date | None, date_to: date | None,
               company: str = "") -> tuple[list[EeeTaxiMisTrip], bool]:
    query = db.query(EeeTaxiMisTrip)
    if date_from:
        query = query.filter(EeeTaxiMisTrip.trip_date >= date_from)
    if date_to:
        query = query.filter(EeeTaxiMisTrip.trip_date <= date_to)
    if company:
        query = query.filter(EeeTaxiMisTrip.company_name == company)
    rows = query.order_by(EeeTaxiMisTrip.trip_date.desc(), EeeTaxiMisTrip.created_at.desc()).limit(MAX_LIST_ROWS + 1).all()
    rows.sort(key=lambda r: (r.trip_date, (r.data or {}).get("pickup_time", "")), reverse=True)
    return rows[:MAX_LIST_ROWS], len(rows) > MAX_LIST_ROWS
