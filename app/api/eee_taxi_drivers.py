"""Driver master: a copy of Truein's staff list, with password-protected edits of the web app's own details."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.eee_taxi_rates import check_edit_password
from app.database import get_db
from app.models import EeeTaxiDriverMaster, User
from app.services.auth import require_permission
from app.services.eee_taxi_driver_sync import attendance_today, sync_drivers, sync_if_stale
from app.services.eee_taxi_drivers import DriverRows, get_driver_rows, save_driver_rows
from app.services.eee_taxi_master_excel import DRIVER_COLUMNS, XLSX_MEDIA_TYPE, build_master_workbook, download_name
from app.services.eee_taxi_mis import id_key
from app.services.truein import is_configured
from app.services.eee_taxi_rates import get_edit_password_hash

router = APIRouter(prefix="/api/eee-taxi/drivers", tags=["eee-taxi"])
_eee_user = require_permission("eee_taxi")


class DriverUpdate(DriverRows):
    edit_password: str = Field(min_length=1, max_length=128)


def _response(db):
    stored = db.get(EeeTaxiDriverMaster, 1)
    rows, today = get_driver_rows(db), attendance_today(db)
    return {
        "rows": [row.model_dump() for row in rows],
        # Truein keeps the list and the status; today's clock-ins are keyed by the driver's Truein ID.
        "from_truein": is_configured(),
        "synced_at": stored.synced_at.isoformat() + "Z" if stored and stored.synced_at else None,
        "sync_error": stored.sync_error if stored else None,
        "attendance": {row.truein_id: today[id_key(row.truein_id)] for row in rows if id_key(row.truein_id) in today},
        "updated_by": stored.updated_by if stored else None,
        "updated_at": stored.updated_at.isoformat() + "Z" if stored else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


@router.get("")
def read(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    sync_if_stale(db)
    return _response(db)


@router.get("/export")
def export(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """The whole driver master as an Excel file, every detail of every driver."""
    content = build_master_workbook("Drivers", DRIVER_COLUMNS, [row.model_dump() for row in get_driver_rows(db)])
    return Response(content, media_type=XLSX_MEDIA_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{download_name("Driver")}"'})


@router.post("/sync")
def sync(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    """Copy the staff list from Truein now (it is also refreshed by itself every half hour of use)."""
    result = sync_drivers(db)
    if not result["ok"]:
        raise HTTPException(502, f"The driver master was not changed. {result['error']}")
    return {**_response(db), "sync": result}


@router.put("")
def save(body: DriverUpdate, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    save_driver_rows(db, body.rows, user.email)
    return _response(db)
