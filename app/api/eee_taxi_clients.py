"""Editable PWC and EY buyer entity masters."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.eee_taxi_rates import check_edit_password
from app.database import get_db
from app.models import User
from app.services.auth import require_permission
from app.services.eee_taxi_client_master import (
    ClientMasterRows, client_master_response, get_client_master, save_client_master,
)
from app.services.eee_taxi_rates import get_edit_password_hash

router = APIRouter(prefix="/api/eee-taxi/clients", tags=["eee-taxi-clients"])
_eee_user = require_permission("eee_taxi")


class ClientMasterUpdateIn(ClientMasterRows):
    edit_password: str = Field(min_length=1, max_length=128)


@router.get("")
def read_clients(
    client_profile: Literal["pwc", "ey"] = Query("pwc"),
    _: User = Depends(_eee_user),
    db: Session = Depends(get_db),
) -> dict:
    data = client_master_response(db, client_profile)
    data["has_edit_password"] = bool(get_edit_password_hash(db))
    return data


@router.put("")
def update_clients(
    body: ClientMasterUpdateIn,
    client_profile: Literal["pwc", "ey"] = Query(...),
    user: User = Depends(_eee_user),
    db: Session = Depends(get_db),
) -> dict:
    check_edit_password(db, body.edit_password, user)
    before = get_client_master(db, client_profile)
    save_client_master(db, client_profile, body.rows, user.email)
    after = get_client_master(db, client_profile)
    logger.info("{} client master saved by {}: {} entities", client_profile.upper(), user.email, len(after))
    data = client_master_response(db, client_profile)
    data["has_edit_password"] = bool(get_edit_password_hash(db))
    data["change_counts"] = {
        "added": len(after.keys() - before.keys()),
        "removed": len(before.keys() - after.keys()),
        "changed": sum(1 for key in before.keys() & after.keys() if before[key] != after[key]),
    }
    return data
