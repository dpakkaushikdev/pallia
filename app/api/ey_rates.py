"""Separate EY rate master, guarded by the existing masters password."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.eee_taxi_rates import check_edit_password
from app.database import get_db
from app.models import User
from app.services.auth import require_permission
from app.services.ey_rates import EyRateCard, ey_rate_response, save_ey_rates

router = APIRouter(prefix="/api/eee-taxi/ey/rates", tags=["eee-taxi-ey"])
_eee_user = require_permission("eee_taxi")


class EyRateUpdate(BaseModel):
    edit_password: str = Field(min_length=1, max_length=128)
    rates: EyRateCard


@router.get("")
def read_rates(user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    return ey_rate_response(db)


@router.put("")
def update_rates(body: EyRateUpdate, user: User = Depends(_eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    save_ey_rates(db, body.rates, user.email)
    return ey_rate_response(db)
