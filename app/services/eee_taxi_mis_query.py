"""MIS pages, counts and totals worked out in the database.

The page shows 50 trips at a time, so only those rows are read in full; the
counts on the tabs, the dashboard cards and the summary table are sums the
database does. Trip values are text inside the JSON ``data`` column. A duty is
Closed when Trip Fare, Drop Time and Total kms are all filled, the same rule
as ``MisTrip.status``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Numeric, and_, case, cast, extract, false, func, not_, or_, tuple_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import EeeTaxiMisTrip as Trip
from app.services.eee_taxi_mis import CLOSING_KEYS, Crew, MasterIndex

VIEWS = ("all", "open", "closed", "fix", "pipeline")
GROUP_KEYS = ("company_name", "date", "dispatcher_name", "driver_name", "cab_no", "pickup_zone")
SEARCH_KEYS = ("guest_name", "driver_name", "driver_id", "cab_no", "route_no", "pickup_address", "drop_location",
               "dispatcher_name", "pickup_zone", "drop_zone", "company_other")
# What decides a driver problem and a cab problem; "hired" is in both because a hired cab or driver is never one.
DRIVER_KEYS = ("driver_id", "driver_name", "driver_contact", "hired", "still_open")
CAB_KEYS = ("cab_no", "hired")
MAX_GROUPS = 500
IST_OFFSET = timedelta(minutes=330)


@dataclass(frozen=True)
class TripFilter:
    """What the page has chosen: dates, client, search text and tab."""
    date_from: date | None = None
    date_to: date | None = None
    company: str = ""
    q: str = ""
    view: str = "all"
    pipeline_from: date | None = None   # the Pipeline tab lists pick-ups from this day on (today when not given)


def ist_today() -> date:
    return (datetime.utcnow() + IST_OFFSET).date()


def field(key: str) -> ColumnElement:
    """One MIS column as text ('' when empty)."""
    return func.coalesce(Trip.data[key].as_string(), "")


def _crew_column(key: str) -> ColumnElement:
    """A ``Crew`` field as the database sees it; ``still_open`` is worked out, the rest are MIS columns."""
    return case((_closed(), ""), else_="Yes") if key == "still_open" else field(key)


def _amount(key: str) -> ColumnElement:
    return func.coalesce(func.sum(cast(func.nullif(field(key), ""), Numeric(16, 2))), 0)


def _closed() -> ColumnElement:
    return and_(*(field(key) != "" for key in CLOSING_KEYS))


def _closed_count() -> ColumnElement:
    return func.coalesce(func.sum(case((_closed(), 1), else_=0)), 0)


def _scope(date_from: date | None, date_to: date | None, company: str, q: str) -> list[ColumnElement]:
    conditions = []
    if date_from:
        conditions.append(Trip.trip_date >= date_from)
    if date_to:
        conditions.append(Trip.trip_date <= date_to)
    if company:
        conditions.append(Trip.company_name == company)
    query = q.strip().lower()
    if query:
        haystack = Trip.company_name
        for key in SEARCH_KEYS:
            haystack = haystack + " " + field(key)
        conditions.append(func.lower(haystack).contains(query, autoescape=True))
    return conditions


def _pipeline_scope(chosen: TripFilter) -> list[ColumnElement]:
    """Bookings still to be picked up, whatever dates the page is showing."""
    return [*_scope(chosen.pipeline_from or ist_today(), None, chosen.company, chosen.q), Trip.in_pipeline.is_(True)]


def _totals(db: Session, conditions: list[ColumnElement]) -> dict:
    trips, closed, kms, fare, total = db.query(
        func.count(Trip.id), _closed_count(), _amount("total_kms"), _amount("trip_fare"),
        _amount("total_trip_fare")).filter(*conditions).one()
    return {"trips": trips, "closed": int(closed), "open": trips - int(closed), "total_kms": float(kms),
            "trip_fare": float(fare), "total_trip_fare": float(total)}


def _crew_check(db: Session, conditions: list[ColumnElement], index: MasterIndex) -> tuple[int, ColumnElement]:
    """How many trips have a driver or cab the masters do not know, and the filter that picks them.

    The masters are matched in Python (Truein ID, then phone, then name), so the
    distinct driver and cab values are read, not the trips themselves.
    """
    columns = [_crew_column(key).label(key) for key in Crew._fields]
    rows = db.query(*columns, func.count(Trip.id)).filter(*conditions).group_by(*Crew._fields)
    flagged, bad_drivers, bad_cabs = 0, set(), set()
    for *values, count in rows:
        crew = Crew(*values)
        driver_bad, cab_bad = index.driver_issue(crew), index.cab_issue(crew)
        if driver_bad:
            bad_drivers.add(tuple(getattr(crew, key) for key in DRIVER_KEYS))
        if cab_bad:
            bad_cabs.add(tuple(getattr(crew, key) for key in CAB_KEYS))
        flagged += count if driver_bad or cab_bad else 0
    by_driver = tuple_(*(_crew_column(key) for key in DRIVER_KEYS)).in_(bad_drivers) if bad_drivers else false()
    by_cab = tuple_(*(field(key) for key in CAB_KEYS)).in_(bad_cabs) if bad_cabs else false()
    return flagged, or_(by_driver, by_cab)


def _view_conditions(db: Session, index: MasterIndex, chosen: TripFilter) -> list[ColumnElement]:
    if chosen.view == "pipeline":
        return _pipeline_scope(chosen)
    ranged = _scope(chosen.date_from, chosen.date_to, chosen.company, chosen.q)
    if chosen.view == "open":
        return [*ranged, not_(_closed())]
    if chosen.view == "closed":
        return [*ranged, _closed()]
    if chosen.view == "fix":
        return [*ranged, _crew_check(db, ranged, index)[1]]
    return ranged


def list_page(db: Session, index: MasterIndex, chosen: TripFilter, page: int, size: int) -> tuple[list[Trip], dict]:
    """One page of the chosen tab, and the counts and totals of the dates on screen."""
    ranged = _scope(chosen.date_from, chosen.date_to, chosen.company, chosen.q)
    summary = _totals(db, ranged)
    need_fixing, fix_filter = _crew_check(db, ranged, index)
    piped = _pipeline_scope(chosen)
    summary.update(need_fixing=need_fixing, pipeline=db.query(func.count(Trip.id)).filter(*piped).scalar())
    conditions = {"open": [*ranged, not_(_closed())], "closed": [*ranged, _closed()],
                  "fix": [*ranged, fix_filter], "pipeline": piped}.get(chosen.view, ranged)
    # The pipeline reads soonest first; every other tab reads latest first.
    order = ((Trip.trip_date, field("pickup_time")) if chosen.view == "pipeline"
             else (Trip.trip_date.desc(), field("pickup_time").desc()))
    rows = (db.query(Trip).filter(*conditions).order_by(*order, Trip.created_at.desc(), Trip.id)
            .offset((page - 1) * size).limit(size).all())
    return rows, summary


def view_total(summary: dict, view: str) -> int:
    return {"open": summary["open"], "closed": summary["closed"], "fix": summary["need_fixing"],
            "pipeline": summary["pipeline"]}.get(view, summary["trips"])


def grouped(db: Session, index: MasterIndex, chosen: TripFilter, group: str) -> list[dict]:
    """The trips of the chosen tab added up by client, date, dispatcher, driver, cab or zone."""
    name = {"company_name": Trip.company_name, "date": Trip.trip_date}.get(group)
    name = field(group) if name is None else name
    trips = func.count(Trip.id)
    query = (db.query(name.label("group_name"), trips, _closed_count(), _amount("total_kms"),
                      _amount("total_trip_fare"))
             .filter(*_view_conditions(db, index, chosen)).group_by("group_name"))
    # Dates read oldest first; when there are more days than fit, the oldest are the ones left out.
    query = query.order_by(name.desc()) if group == "date" else query.order_by(trips.desc(), "group_name")
    rows = [{"name": value.isoformat() if isinstance(value, date) else value, "trips": count,
             "open": count - int(closed), "total_kms": float(kms), "total_trip_fare": float(fare)}
            for value, count, closed, kms, fare in query.limit(MAX_GROUPS)]
    return rows[::-1] if group == "date" else rows


def dashboard(db: Session, year: int | None, month: int | None) -> dict:
    """Totals of one month, one year, one month across the years, or everything.

    Bookings still in the pipeline are open trips and are counted; ``pipeline`` says how many.
    """
    conditions = []
    if year and month:
        conditions += [Trip.trip_date >= date(year, month, 1),
                       Trip.trip_date < date(year + month // 12, month % 12 + 1, 1)]
    elif year:
        conditions += [Trip.trip_date >= date(year, 1, 1), Trip.trip_date < date(year + 1, 1, 1)]
    elif month:
        conditions.append(extract("month", Trip.trip_date) == month)
    first, last = db.query(func.min(Trip.trip_date), func.max(Trip.trip_date)).one()
    years = list(range(last.year, first.year - 1, -1)) if first else []
    in_pipeline = db.query(func.count(Trip.id)).filter(*conditions, Trip.in_pipeline.is_(True)).scalar()
    return {**_totals(db, conditions), "pipeline": in_pipeline, "years": years}
