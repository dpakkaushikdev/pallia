"""Driver master reads and password-protected saves."""
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.eee_taxi_rates import check_edit_password
from app.database import get_db
from app.models import EeeTaxiDriverMaster, User
from app.services.auth import require_permission
from app.services.eee_taxi_drivers import DriverRows, get_driver_rows, save_driver_rows
from app.services.eee_taxi_rates import get_edit_password_hash

router = APIRouter(prefix="/api/eee-taxi/drivers", tags=["eee-taxi"])
_eee_user = require_permission("eee_taxi")


class DriverUpdate(DriverRows):
    edit_password: str = Field(min_length=1, max_length=128)


def _response(db):
    stored = db.get(EeeTaxiDriverMaster, 1)
    return {
        "rows": [row.model_dump() for row in get_driver_rows(db)],
        "updated_by": stored.updated_by if stored else None,
        "updated_at": stored.updated_at.isoformat() + "Z" if stored else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


@router.get("")
def read(_: User = Depends(_eee_user), db: Session = Depends(get_db)):
    return _response(db)


@router.put("")
def save(body: DriverUpdate, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    save_driver_rows(db, body.rows, user.email)
    return _response(db)
