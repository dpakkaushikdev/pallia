"""EEE-Taxi MIS: one row per corporate trip, entered by hand or uploaded.

Column order and headers follow the team's MMT_MIS.xlsx sheet. Every value is
kept as text (dates as YYYY-MM-DD, times as HH:MM) so a half-filled trip can
still be saved; drivers and cabs are checked against the masters and any
mismatch is reported with the trip instead of blocking the save.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiMisTrip
from app.services.eee_taxi_cost_centres import normalize_vehicle_no
from app.services.eee_taxi_drivers import DriverRow, get_driver_rows
from app.services.eee_taxi_mis_zones import OTHERS, canonical_company, canonical_zone, guess_zone
from app.services.eee_taxi_vehicles import get_vehicle_rows

# (key, Excel header) in the order of the team's "Corp Trips Mis" month sheets.
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
    ("night_charge", "Night Charge 11pm to 5am"), ("trip_fare", "Trip Fare"), ("gst", "GST"), ("mcd", "MCD"),
    ("parking", "Parking"), ("toll", "Toll"), ("total_trip_fare", "Total Trip Fare"), ("driver_hours", "Driver Hours"),
    ("cash_to_receive", "Cash to be Recieved"), ("remarks", "Remarks"), ("mmt_base_cost", "MMT Base Cost"),
    ("mmt_prepaid_amount", "MMT Peripaid Amt"), ("duty_closed_by", "Duty closed by"),
    ("b2c_payment_status", "B2C payment Status"),
]
DATE_KEYS = ("date", "trip_end_date")
TIME_KEYS = ("pickup_time", "drop_time")
NUMBER_KEYS = (
    "start_odometer", "end_odometer", "total_kms", "dead_run_kms", "extra_km_charges",
    "extra_time_charges", "night_charge", "trip_fare", "gst", "mcd", "parking", "toll",
    "total_trip_fare", "cash_to_receive", "mmt_base_cost", "mmt_prepaid_amount",
)
# Trip Fare already holds the package and extras; these are added on top for the total.
TOTAL_KEYS = ("trip_fare", "gst", "mcd", "parking", "toll")
# What a dispatcher enters when a trip is booked; the rest is filled when the duty closes.
DISPATCH_KEYS = (
    "dispatcher_name", "hired", "driver_id", "driver_name", "driver_contact", "cab_no", "company_name", "company_other",
    "guest_name",
    "pickup_address", "pickup_zone", "pickup_time", "drop_location", "drop_zone", "hub_out",
)
# A trip stays Open until these are filled in.
CLOSING_KEYS = ("trip_fare", "drop_time", "total_kms")
STATUSES = ("Open", "Closed")
MAX_LIST_ROWS = 8000   # a month is about 3,600 trips

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
    # "Yes" when the cab and driver were hired from outside for this trip, so neither is in the masters.
    hired: str = Field(default="", max_length=20)   # not a sheet column
    route_no: str = Field(default="", max_length=64)
    company_name: str = Field(min_length=1, max_length=32)
    company_other: Short   # the client's name when Company Name is Others; not a sheet column
    guest_name: Long   # a shared cab lists every guest: "A/B/C"
    pickup_address: Long
    pickup_zone: Short
    pickup_time: str = ""
    drop_location: Long
    drop_zone: Short
    drop_time: str = ""
    trip_end_date: str = ""
    trip_duration: str = Field(default="", max_length=20)
    hub_out: Short   # a place ("Vasant Kunj") or a note ("20km hub to hub"), not a time
    hub_in: Short
    start_odometer: str = ""
    end_odometer: str = ""
    total_kms: str = ""
    dead_run_kms: str = ""
    package: Short
    extra_km_charges: str = ""
    extra_time_charges: str = ""
    night_charge: str = ""
    trip_fare: str = ""
    gst: str = ""
    mcd: str = ""
    parking: str = ""
    toll: str = ""
    total_trip_fare: str = ""
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

    @field_validator("hired")
    @classmethod
    def yes_or_blank(cls, value: str) -> str:
        return "Yes" if value.lower() in ("yes", "y", "true", "hired") else ""

    @field_validator("company_name")
    @classmethod
    def company(cls, value: str) -> str:
        """Spelled as in the client list ('mongo db' -> 'MongoDB'); other names are refused."""
        match = canonical_company(value)
        if match is None:
            raise ValueError(f"'{value}' is not in the client list.")
        return match

    @field_validator("pickup_zone", "drop_zone")
    @classmethod
    def zone(cls, value: str) -> str:
        return canonical_zone(value)

    @field_validator("route_no")
    @classmethod
    def no_dash(cls, value: str) -> str:
        return "" if value == "-" else value

    @property
    def missing(self) -> list[str]:
        """Sheet names of the closing fields still empty."""
        return [COLUMN_LABELS[key] for key in CLOSING_KEYS if not getattr(self, key)]

    @property
    def status(self) -> str:
        return "Open" if self.missing else "Closed"

    @property
    def still_open(self) -> str:
        """'Yes' while the duty is not closed (text, so a grouped database read can carry the same flag)."""
        return "Yes" if self.missing else ""

    @model_validator(mode="after")
    def derived_values(self):
        if self.company_name != OTHERS:
            self.company_other = ""
        duration = trip_duration(self.date, self.pickup_time, self.trip_end_date, self.drop_time)
        if duration:
            self.trip_duration = duration
        if self.start_odometer and self.end_odometer:
            kms = Decimal(self.end_odometer) - Decimal(self.start_odometer)
            if kms < 0:
                raise ValueError("End Odometer is lower than Start Odometer.")
            # A typed Total kms stays: the old sheets add hub and dead-run km on top of the odometers.
            self.total_kms = self.total_kms or format_number(str(kms))
        if self.trip_fare:
            total = sum(Decimal(getattr(self, key) or "0") for key in TOTAL_KEYS)
            self.total_trip_fare = format_number(str(total))
        self.pickup_zone = self.pickup_zone or guess_zone(self.pickup_address)
        self.drop_zone = self.drop_zone or guess_zone(self.drop_location)
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
MAX_FIX_ROWS = 200


class Crew(NamedTuple):
    """The driver and cab of a trip: all that the check against the masters reads."""
    driver_id: str
    driver_name: str
    driver_contact: str
    cab_no: str
    hired: str = ""
    still_open: str = "Yes"


def name_key(name: str) -> str:
    """'Umakant  Dwivedi.' and 'UMAKANTDWIVEDI' compare equal."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def name_fits(typed: str, driver: DriverRow) -> bool:
    """'Faisal' fits 'FAISAL KHAN'; a different name, or one too short to tell, does not."""
    key, full = name_key(typed), name_key(driver.name)
    return bool(key) and (key == full or (len(key) >= MIN_NAME_PREFIX and full.startswith(key)))


def id_key(value: str) -> str:
    """A Driver ID / Truein ID without case or punctuation ('eeed/vk-0044' = 'EEED/VK/0044'); 'N/A' is no ID."""
    key = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return "" if key == "NA" else key


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
    by_phone: dict[str, list[DriverRow]] = field(default_factory=dict)   # two drivers can share a number
    by_name: dict[str, DriverRow] = field(default_factory=dict)
    all_names: list[tuple[str, DriverRow]] = field(default_factory=list)
    plates: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, db: Session) -> "MasterIndex":
        index = cls(plates={row.vehicle_no for row in get_vehicle_rows(db)})
        names: dict[str, list[DriverRow]] = {}
        for driver in get_driver_rows(db):
            if id_key(driver.truein_id):
                index.by_truein[id_key(driver.truein_id)] = driver
            for phone in (driver.mobile, driver.alternate_mobile):
                owners = index.by_phone.setdefault(normalize_phone(phone), []) if normalize_phone(phone) else None
                if owners is not None and driver not in owners:
                    owners.append(driver)
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

    def find_driver(self, trip: MisTrip | Crew) -> DriverRow | None:
        """The Driver ID (Truein ID) decides when the trip has one.

        Without one (an uploaded file has none), the mobile and the name must both
        be the same master driver's; either alone is not enough.
        """
        key = id_key(trip.driver_id)
        if key:
            return self.by_truein.get(key)
        fits = [driver for driver in self.by_phone.get(trip.driver_contact, []) if name_fits(trip.driver_name, driver)]
        return fits[0] if len(fits) == 1 else None

    def _likely(self, trip: MisTrip | Crew) -> DriverRow | None:
        """Who the master probably means, named in the message on a trip that did not match."""
        owners = self.by_phone.get(trip.driver_contact, [])
        return owners[0] if len(owners) == 1 else self.find_by_name(trip.driver_name)

    def _half_match(self, trip: MisTrip | Crew) -> str:
        """Why a driver with no ID did not match, so the name or the mobile can be put right."""
        owners = self.by_phone.get(trip.driver_contact, [])
        named = self.find_by_name(trip.driver_name)
        if owners:
            return f"Driver mobile is {owners[0].name}'s in the master, but the name is {trip.driver_name or 'missing'}"
        if named and trip.driver_contact:
            return f"Driver {named.name} is in the master with a different mobile"
        if named:
            return f"Driver {named.name} is in the master, but the trip has no mobile to confirm it"
        return "Driver not in driver master"

    def fill(self, trip: MisTrip) -> MisTrip:
        """Copy the matched driver's Truein ID, master name and mobile onto the trip."""
        # A hired driver keeps what the dispatcher typed, even when a fleet driver has the same name.
        driver = None if trip.hired else self.find_driver(trip)
        if driver is None:
            return trip
        return trip.model_copy(update={
            "driver_id": driver.truein_id or trip.driver_id,
            "driver_name": driver.name,
            "driver_contact": normalize_phone(driver.mobile) or trip.driver_contact,
        })

    def driver_issue(self, trip: MisTrip | Crew) -> str:
        if trip.hired:
            # Hired with the cab: not in the master by design, so only an empty driver is worth a flag.
            return "" if trip.driver_name or trip.driver_contact else "Driver missing"
        typed_id = id_key(trip.driver_id)
        if not (typed_id or trip.driver_name or trip.driver_contact):
            return "Driver missing"
        driver = self.find_driver(trip)
        if driver is None and typed_id:
            # Say who the master thinks this is, so the wrong side (trip or master) can be corrected.
            known = self._likely(trip)
            hint = f" ({known.name} is {known.truein_id or 'without a Truein ID'} there)" if known else ""
            return f"Driver ID {trip.driver_id} not in driver master{hint}"
        if driver is None:
            return self._half_match(trip)
        # Someone who has since left drove their closed trips rightly; only a trip still to run is a problem.
        return "Driver is Deactive in the master" if driver.status == "Deactive" and trip.still_open else ""

    def cab_issue(self, trip: MisTrip | Crew) -> str:
        if not trip.cab_no:
            return "Cab No missing"
        return "" if trip.hired or trip.cab_no in self.plates else f"Cab {trip.cab_no} not in vehicle master"

    def issues(self, trip: MisTrip | Crew) -> list[str]:
        return [problem for problem in (self.driver_issue(trip), self.cab_issue(trip)) if problem]


@dataclass
class FixList:
    """Every driver and cab in an uploaded file that the masters do not know, with its trips."""
    index: MasterIndex
    trips: int = 0
    driver_trips: int = 0
    cab_trips: int = 0
    drivers: Counter = field(default_factory=Counter)
    cabs: Counter = field(default_factory=Counter)

    def note(self, trip: MisTrip) -> None:
        driver, cab = self.index.driver_issue(trip), self.index.cab_issue(trip)
        if driver:
            self.drivers[(trip.driver_name, trip.driver_contact, driver)] += 1
        if cab:
            self.cabs[(trip.cab_no, cab)] += 1
        self.trips += bool(driver or cab)
        self.driver_trips += bool(driver)
        self.cab_trips += bool(cab)

    def report(self) -> dict:
        return {
            "need_fixing": self.trips, "drivers_to_fix": len(self.drivers), "driver_trips": self.driver_trips,
            "cabs_to_fix": len(self.cabs), "cab_trips": self.cab_trips,
            "fix_drivers": [{"name": name, "mobile": mobile, "problem": problem, "trips": count}
                            for (name, mobile, problem), count in self.drivers.most_common(MAX_FIX_ROWS)],
            "fix_cabs": [{"cab_no": cab, "problem": problem, "trips": count}
                         for (cab, problem), count in self.cabs.most_common(MAX_FIX_ROWS)],
        }


def trip_response(row: EeeTaxiMisTrip, index: MasterIndex) -> dict:
    trip = MisTrip(**row.data)
    return {
        **trip.model_dump(), "id": row.id, "source": row.source, "status": trip.status, "missing": trip.missing,
        "in_pipeline": bool(row.in_pipeline),
        "created_by": row.created_by, "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() + "Z", "issues": index.issues(trip),
    }


def find_duplicate(db: Session, trip: MisTrip, exclude_id: str | None = None) -> EeeTaxiMisTrip | None:
    """The same trip entered twice.

    An MMT booking id is used once ever. Other clients reuse a route number
    (often the guest's phone) day after day, so there a duplicate is the same
    client, route, date and pick-up time.
    """
    if not trip.route_no:
        return None
    query = db.query(EeeTaxiMisTrip).filter(
        EeeTaxiMisTrip.company_name == trip.company_name, EeeTaxiMisTrip.route_no == trip.route_no)
    if trip.company_name != "MMT":
        query = query.filter(EeeTaxiMisTrip.trip_date == date.fromisoformat(trip.date))
    if exclude_id:
        query = query.filter(EeeTaxiMisTrip.id != exclude_id)
    if trip.company_name == "MMT":
        return query.first()
    return next((row for row in query if (row.data or {}).get("pickup_time", "") == trip.pickup_time), None)


def copy_for_date(trip: MisTrip, new_date: str) -> MisTrip:
    """A fresh open booking of the same run on another day (shuttle rosters repeat daily)."""
    kept = {key: getattr(trip, key) for key in DISPATCH_KEYS}
    # A phone number used as the route number stays the same; a booking number does not.
    route_no = trip.route_no if re.fullmatch(r"\d{10}", trip.route_no) else ""
    return MisTrip(date=new_date, route_no=route_no, **kept)


def stamp_closer(trip: MisTrip, was_open: bool, closer: str) -> MisTrip:
    """Fill Duty closed by with whoever closes the trip, unless it was typed in."""
    if was_open and trip.status == "Closed" and not trip.duty_closed_by:
        return trip.model_copy(update={"duty_closed_by": closer[:120]})
    return trip


def store_trip(row: EeeTaxiMisTrip, trip: MisTrip, editor: str) -> EeeTaxiMisTrip:
    row.data = trip.model_dump()
    row.company_name = trip.company_name
    row.route_no = trip.route_no or None
    row.trip_date = date.fromisoformat(trip.date)
    row.updated_by = editor
    row.updated_at = datetime.utcnow()
    if trip.status == "Closed":
        row.in_pipeline = False   # the pipeline lists trips still to be run
    return row


def list_trips(db: Session, date_from: date | None, date_to: date | None,
               company: str = "") -> tuple[list[EeeTaxiMisTrip], bool]:
    """Every trip of a date range, for the Excel download. The page itself reads ``eee_taxi_mis_query``."""
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
