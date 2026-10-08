"""Vehicle master reads, CSV review, and password-protected replacement."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.eee_taxi_rates import check_edit_password
from app.database import get_db
from app.models import EeeTaxiVehicleMaster, User
from app.services.auth import require_permission
from app.services.eee_taxi_rates import get_edit_password_hash
from app.services.eee_taxi_vehicles import VehicleRows, get_vehicle_rows, parse_vehicle_csv, save_vehicle_rows

router = APIRouter(prefix="/api/eee-taxi/vehicles", tags=["eee-taxi"])
_eee_user = require_permission("eee_taxi")


class VehicleUpdate(VehicleRows):
    edit_password: str = Field(min_length=1, max_length=128)


def _response(db):
    stored = db.get(EeeTaxiVehicleMaster, 1)
    return {
        "rows": [row.model_dump() for row in get_vehicle_rows(db)],
        "updated_by": stored.updated_by if stored else None,
        "updated_at": stored.updated_at.isoformat() + "Z" if stored else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


@router.get("")
def read(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    return _response(db)


@router.post("/import-csv")
def preview(file: UploadFile = File(...), _: User = Depends(_eee_user)):
    data = file.file.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "Vehicle CSV must be smaller than 2 MB.")
    try:
        return {"rows": [row.model_dump() for row in parse_vehicle_csv(data)]}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("")
def save(body: VehicleUpdate, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    save_vehicle_rows(db, body.rows, user.email)
    return _response(db)
