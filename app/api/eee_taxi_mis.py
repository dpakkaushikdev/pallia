"""EEE-Taxi MIS: list, add, edit, delete and upload corporate trips; MongoDB rate list."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EeeTaxiMisClient, EeeTaxiMisTrip, User
from app.api.eee_taxi_rates import check_edit_password
from app.services.auth import require_permission
from app.services.eee_taxi_drivers import get_driver_rows
from app.services.eee_taxi_mis import (
    STATUSES, MasterIndex, MisTrip, copy_for_date, find_duplicate, list_trips, parse_trip, stamp_closer,
    store_trip, trip_response,
)
from app.services.eee_taxi_mis_excel import build_mis_workbook, parse_mmt_export
from app.services.eee_taxi_mis_mongodb import COMPANY as MONGODB, merge_file_values, parse_mongodb_report
from app.services.eee_taxi_mis_query import (
    TripFilter, dashboard, grouped, ist_today, last_route_of_guest, list_page, view_total,
)
from app.services.eee_taxi_mis_zones import (
    OTHERS, ZONES, all_companies, canonical_company, new_company_name, set_added_companies,
)
from app.services.eee_taxi_vehicles import get_vehicle_rows
from app.services.mongodb_rates import MongoRateList, get_mongodb_rates, mongodb_rate_response, save_mongodb_rates

_eee_user = require_permission("eee_taxi")


def _load_clients(db: Session = Depends(get_db)) -> None:
    """Every MIS request starts with the current client list (each server instance has its own memory)."""
    set_added_companies([name for (name,) in db.query(EeeTaxiMisClient.name).order_by(EeeTaxiMisClient.created_at)])


router = APIRouter(prefix="/api/eee-taxi/mis", tags=["eee-taxi"], dependencies=[Depends(_load_clients)])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
GUEST_LOOKBACK_DAYS = 90
View = Literal["all", "open", "closed", "fix", "pipeline"]


class TripIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=3000)


class PipelineChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=500)
    in_pipeline: bool


class MongoRateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_password: str = Field(min_length=1, max_length=128)
    rates: MongoRateList


class NewClient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class TripCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=500)
    date: date


def _matches(trip: dict, query: str) -> bool:
    keys = ("guest_name", "driver_name", "driver_id", "cab_no", "route_no", "pickup_address", "drop_location",
            "company_other")
    return query in " ".join(trip.get(k, "") for k in keys).lower()


def _client(company: str) -> str:
    """Client list spelling of a filter value ('' for all clients)."""
    return canonical_company(company) or company.strip()


def _filtered(db: Session, date_from: date | None, date_to: date | None, company: str, q: str, status: str = ""):
    rows, truncated = list_trips(db, date_from, date_to, _client(company))
    index = MasterIndex.build(db)
    trips = [trip_response(row, index) for row in rows]
    query = q.strip().lower()
    return [t for t in trips if (not query or _matches(t, query)) and (not status or t["status"] == status)], truncated


def _closer(user: User) -> str:
    return user.full_name or user.email


def _already_entered(conflict: EeeTaxiMisTrip) -> HTTPException:
    return HTTPException(409, f"This trip is already in the MIS: {conflict.company_name} route "
                              f"{conflict.route_no} on {conflict.trip_date:%d/%m/%Y}.")


def _chosen(date_from: date | None = None, date_to: date | None = None,
            company: str = Query("", max_length=32), q: str = Query("", max_length=120),
            view: View = "all", pipeline_from: date | None = None) -> TripFilter:
    return TripFilter(date_from, date_to, _client(company), q, view, pipeline_from)


@router.get("")
def read(chosen: TripFilter = Depends(_chosen), page: int = Query(1, ge=1, le=100000),
         page_size: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
         _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """One page of a tab (All, Open, Closed, Needs fixing, Pipeline).

    ``summary`` counts every tab for the dates, client and search chosen, so the
    page can show them without reading the trips. The Pipeline tab ignores the
    dates: it lists marked trips from today (``pipeline_from``) onward, soonest first.
    """
    index = MasterIndex.build(db)
    rows, summary = list_page(db, index, chosen, page, page_size)
    return {"trips": [trip_response(row, index) for row in rows], "page": page, "page_size": page_size,
            "total": view_total(summary, chosen.view), "summary": summary}


@router.get("/dashboard")
def read_dashboard(year: int | None = Query(None, ge=2000, le=2100), month: int | None = Query(None, ge=1, le=12),
                   _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Trips, kms and fare of a month, a year or everything; ``years`` lists the years that have trips."""
    return dashboard(db, year, month)


@router.get("/summary")
def read_summary(group: Literal["company_name", "date", "dispatcher_name", "driver_name", "cab_no", "pickup_zone"],
                 chosen: TripFilter = Depends(_chosen),
                 _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """The chosen tab's trips added up by client, date, dispatcher, driver, cab or pickup zone."""
    return {"groups": grouped(db, MasterIndex.build(db), chosen, group)}


@router.get("/guest-route")
def guest_route(guest: str = Query(min_length=1, max_length=500), company: str = Query("", max_length=32),
                _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """The route of this guest's latest trip in the last three months (``route`` is null for a new guest)."""
    since = ist_today() - timedelta(days=GUEST_LOOKBACK_DAYS)
    return {"route": last_route_of_guest(db, guest, _client(company), since)}


@router.get("/lookups")
def lookups(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Choices for the trip form: drivers, cabs and companies."""
    return {
        "drivers": [{"name": d.name, "truein_id": d.truein_id, "mobile": d.mobile, "status": d.status}
                    for d in get_driver_rows(db)],
        "cabs": sorted(row.vehicle_no for row in get_vehicle_rows(db)),
        "companies": list(all_companies()), "statuses": list(STATUSES), "zones": list(ZONES),
    }


@router.post("/clients", status_code=201)
def add_client(body: NewClient, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Add a client to the list every dispatcher chooses from."""
    try:
        name = new_company_name(body.name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(EeeTaxiMisClient(name=name, created_by=user.email))
    db.commit()
    _load_clients(db)
    return {"name": name, "companies": list(all_companies())}


@router.get("/export")
def export(date_from: date | None = None, date_to: date | None = None,
           company: str = Query("", max_length=32), q: str = Query("", max_length=120),
           status: Literal["", "Open", "Closed"] = "",
           _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    trips, _truncated = _filtered(db, date_from, date_to, company, q, status)
    trips.sort(key=lambda t: (t["date"], t["pickup_time"]))
    # The sheet has no column for a one-off client's name, so it rides in Company Name.
    trips = [{**t, "company_name": f"{OTHERS} - {t['company_other']}"} if t["company_other"] else t for t in trips]
    # Nor one for a hired cab; the old sheets wrote such trips with a word in the Driver ID column.
    trips = [{**t, "driver_id": "Hired"} if t["hired"] and not t["driver_id"] else t for t in trips]
    label = re.sub(r"[^A-Za-z0-9]+", "", _client(company)) if _client(company) in all_companies() else "All"
    name = f"MIS_{label}_{date_from or 'start'}_to_{date_to or 'latest'}.xlsx"
    return Response(build_mis_workbook(trips), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/mongodb-rates")
def read_mongodb_rates(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    return mongodb_rate_response(db)


@router.put("/mongodb-rates")
def update_mongodb_rates(body: MongoRateUpdate, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    save_mongodb_rates(db, body.rates, user.email)
    return mongodb_rate_response(db)


def _parse(body: dict) -> MisTrip:
    try:
        return parse_trip(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201)
def create(body: dict = Body(...), pipeline: bool = False,
           user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    index = MasterIndex.build(db)
    trip = stamp_closer(index.fill(_parse(body)), True, _closer(user))
    conflict = find_duplicate(db, trip)
    if conflict:
        raise _already_entered(conflict)
    row = store_trip(EeeTaxiMisTrip(source="manual", created_by=user.email, in_pipeline=pipeline), trip, user.email)
    db.add(row)
    db.commit()
    return trip_response(row, index)


@router.put("/{trip_id}")
def update(trip_id: str, body: dict = Body(...), user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    row = db.get(EeeTaxiMisTrip, trip_id)
    if row is None:
        raise HTTPException(404, "Trip not found. It may have been deleted.")
    index = MasterIndex.build(db)
    was_open = MisTrip(**row.data).status == "Open"
    trip = stamp_closer(index.fill(_parse(body)), was_open, _closer(user))
    conflict = find_duplicate(db, trip, exclude_id=trip_id)
    if conflict:
        raise _already_entered(conflict)
    store_trip(row, trip, user.email)
    db.commit()
    return trip_response(row, index)


@router.post("/delete")
def delete(body: TripIds, _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    deleted = db.query(EeeTaxiMisTrip).filter(EeeTaxiMisTrip.id.in_(body.ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.post("/pipeline")
def set_pipeline(body: PipelineChange, _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Show or stop showing trips in the Pipeline view; the trips themselves are not changed."""
    changed = 0
    for row in db.query(EeeTaxiMisTrip).filter(EeeTaxiMisTrip.id.in_(body.ids)):
        # A closed trip has been run, so it has no place among the bookings still to come.
        if body.in_pipeline and MisTrip(**row.data).status == "Closed":
            continue
        row.in_pipeline = body.in_pipeline
        changed += 1
    db.commit()
    return {"changed": changed}


@router.post("/copy")
def copy(body: TripCopy, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Book the chosen trips again on another date, as open trips with the dispatch details only."""
    rows = db.query(EeeTaxiMisTrip).filter(EeeTaxiMisTrip.id.in_(body.ids)).all()
    rows.sort(key=lambda r: (r.data or {}).get("pickup_time", ""))
    added, skipped = 0, 0
    for row in rows:
        trip = copy_for_date(MisTrip(**row.data), body.date.isoformat())
        if find_duplicate(db, trip):
            skipped += 1
            continue
        db.add(store_trip(EeeTaxiMisTrip(source="copy", created_by=user.email), trip, user.email))
        db.flush()
        added += 1
    db.commit()
    return {"added": added, "skipped_existing": skipped}


@router.post("/upload-mmt")
def upload_mmt(file: UploadFile = File(...), dispatcher_name: str = Form("", max_length=120),
               user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Add every trip in an MMT Trip Report; trips already in the MIS are left as they are."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "MMT file must be smaller than 5 MB.")
    try:
        trips, errors = parse_mmt_export(data, dispatcher_name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = {route for (route,) in db.query(EeeTaxiMisTrip.route_no)
                .filter(EeeTaxiMisTrip.company_name == "MMT", EeeTaxiMisTrip.route_no.isnot(None))}
    index = MasterIndex.build(db)
    added, skipped, need_fixing = [], 0, 0
    for trip in trips:
        if trip.route_no and trip.route_no in existing:
            skipped += 1
            continue
        trip = index.fill(trip)
        db.add(store_trip(EeeTaxiMisTrip(source="mmt_upload", created_by=user.email), trip, user.email))
        existing.add(trip.route_no)
        added.append(trip.date)
        need_fixing += bool(index.issues(trip))
    db.commit()
    return {"added": len(added), "skipped_existing": skipped, "need_fixing": need_fixing, "errors": errors,
            "first_date": min(added, default=None), "last_date": max(added, default=None)}


@router.post("/upload-mongodb")
def upload_mongodb(file: UploadFile = File(...), dispatcher_name: str = Form("", max_length=120),
                   user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Add the trips of a MongoDB raw data report, one per Trip ID.

    A trip already in the MIS is left alone, unless it is still open and the
    report now closes it (an evening trip uploaded before it ran).
    """
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "MongoDB file must be smaller than 5 MB.")
    try:
        trips, messages = parse_mongodb_report(data, dispatcher_name.strip(), get_mongodb_rates(db))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Only the trips named in the file are read back, not every MongoDB trip ever stored.
    existing = {row.route_no: row for row in db.query(EeeTaxiMisTrip)
                .filter(EeeTaxiMisTrip.company_name == MONGODB,
                        EeeTaxiMisTrip.route_no.in_([trip.route_no for trip in trips]))}
    index = MasterIndex.build(db)
    dates, added, updated, skipped, still_open, need_fixing = [], 0, 0, 0, 0, 0
    for trip in trips:
        row = existing.get(trip.route_no)
        if row is None:
            trip = index.fill(trip)
            row = store_trip(EeeTaxiMisTrip(source="mongodb", created_by=user.email), trip, user.email)
            db.add(row)
            existing[trip.route_no] = row
            added += 1
        elif MisTrip(**row.data).status == "Open" and trip.status == "Closed":
            trip = index.fill(parse_trip(merge_file_values(row.data, trip)))
            store_trip(row, trip, user.email)
            updated += 1
        else:
            skipped += 1
            continue
        dates.append(trip.date)
        still_open += trip.status == "Open"
        need_fixing += bool(index.issues(trip))
    db.commit()
    return {"added": added, "updated": updated, "skipped_existing": skipped, "still_open": still_open,
            "need_fixing": need_fixing, "errors": messages,
            "first_date": min(dates, default=None), "last_date": max(dates, default=None)}
