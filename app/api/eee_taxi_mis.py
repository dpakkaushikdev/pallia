"""EEE-Taxi MIS: list, add, edit, delete and upload corporate trips."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EeeTaxiMisTrip, User
from app.services.auth import require_permission
from app.services.eee_taxi_drivers import get_driver_rows
from app.services.eee_taxi_mis import (
    COMPANIES, MasterIndex, MisTrip, find_route_conflict, list_trips, parse_trip, store_trip, trip_response,
)
from app.services.eee_taxi_mis_excel import build_mis_workbook, parse_mmt_export
from app.services.eee_taxi_vehicles import get_vehicle_rows

router = APIRouter(prefix="/api/eee-taxi/mis", tags=["eee-taxi"])
_eee_user = require_permission("eee_taxi")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class TripIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=3000)


def _matches(trip: dict, query: str) -> bool:
    keys = ("guest_name", "driver_name", "driver_id", "cab_no", "route_no", "pickup_address", "drop_location")
    return query in " ".join(trip.get(k, "") for k in keys).lower()


def _filtered(db: Session, date_from: date | None, date_to: date | None, company: str, q: str):
    rows, truncated = list_trips(db, date_from, date_to, company)
    index = MasterIndex.build(db)
    trips = [trip_response(row, index) for row in rows]
    query = q.strip().lower()
    return ([t for t in trips if _matches(t, query)] if query else trips), truncated


def _amount(trips: list[dict], key: str) -> float:
    return round(sum(float(t[key]) for t in trips if t.get(key)), 2)


def _route_taken(conflict: EeeTaxiMisTrip) -> HTTPException:
    return HTTPException(409, f"Route No {conflict.route_no} is already in the MIS for "
                              f"{conflict.company_name} ({conflict.trip_date:%d/%m/%Y}).")


@router.get("")
def read(date_from: date | None = None, date_to: date | None = None,
         company: str = Query("", max_length=32), q: str = Query("", max_length=120),
         _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    trips, truncated = _filtered(db, date_from, date_to, company, q)
    return {
        "trips": trips, "truncated": truncated,
        "summary": {"trips": len(trips), "trip_fare": _amount(trips, "trip_fare"),
                    "total_kms": _amount(trips, "total_kms"),
                    "need_fixing": sum(1 for t in trips if t["issues"])},
    }


@router.get("/lookups")
def lookups(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Choices for the trip form: drivers, cabs and companies."""
    return {
        "drivers": [{"name": d.name, "truein_id": d.truein_id, "mobile": d.mobile, "status": d.status}
                    for d in get_driver_rows(db)],
        "cabs": sorted(row.vehicle_no for row in get_vehicle_rows(db)),
        "companies": list(COMPANIES),
    }


@router.get("/export")
def export(date_from: date | None = None, date_to: date | None = None,
           company: str = Query("", max_length=32), q: str = Query("", max_length=120),
           _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    trips, _truncated = _filtered(db, date_from, date_to, company, q)
    trips.sort(key=lambda t: (t["date"], t["pickup_time"]))
    label = company.upper() if company.upper() in COMPANIES else "All"
    name = f"MIS_{label}_{date_from or 'start'}_to_{date_to or 'latest'}.xlsx"
    return Response(build_mis_workbook(trips), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _parse(body: dict) -> MisTrip:
    try:
        return parse_trip(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201)
def create(body: dict = Body(...), user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    index = MasterIndex.build(db)
    trip = index.fill(_parse(body))
    conflict = find_route_conflict(db, trip)
    if conflict:
        raise _route_taken(conflict)
    row = store_trip(EeeTaxiMisTrip(source="manual", created_by=user.email), trip, user.email)
    db.add(row)
    db.commit()
    return trip_response(row, index)


@router.put("/{trip_id}")
def update(trip_id: str, body: dict = Body(...), user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    row = db.get(EeeTaxiMisTrip, trip_id)
    if row is None:
        raise HTTPException(404, "Trip not found. It may have been deleted.")
    index = MasterIndex.build(db)
    trip = index.fill(_parse(body))
    conflict = find_route_conflict(db, trip, exclude_id=trip_id)
    if conflict:
        raise _route_taken(conflict)
    store_trip(row, trip, user.email)
    db.commit()
    return trip_response(row, index)


@router.post("/delete")
def delete(body: TripIds, _: User = Depends(_eee_user), db: Session = Depends(get_db)):
    deleted = db.query(EeeTaxiMisTrip).filter(EeeTaxiMisTrip.id.in_(body.ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


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
