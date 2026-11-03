"""EEE-Taxi driver master: name, Truein ID, contact and work details.

Aadhaar, bank and document-image fields from the fleet export are deliberately
not stored.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiDriverMaster


class DriverRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    driver_id: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=120)
    truein_id: str = Field(default="", max_length=40)
    mobile: str = Field(default="", max_length=20)
    alternate_mobile: str = Field(default="", max_length=20)
    father_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)
    status: str = Field(default="", max_length=40)
    availability: str = Field(default="", max_length=40)
    branch: str = Field(default="", max_length=80)
    department: str = Field(default="", max_length=80)
    date_of_joining: str = Field(default="", max_length=20)
    dl_expiry: str = Field(default="", max_length=20)
    emergency_contact_name: str = Field(default="", max_length=120)
    emergency_contact_number: str = Field(default="", max_length=20)
    emergency_contact_relationship: str = Field(default="", max_length=40)


class DriverRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[DriverRow] = Field(max_length=5000)

    @model_validator(mode="after")
    def unique_ids(self):
        seen = set()
        for row in self.rows:
            if not row.driver_id:
                row.driver_id = uuid4().hex[:8]
            if row.driver_id in seen:
                raise ValueError(f"Driver ID {row.driver_id} appears more than once.")
            seen.add(row.driver_id)
        return self


def get_driver_rows(db: Session) -> list[DriverRow]:
    stored = db.get(EeeTaxiDriverMaster, 1)
    return DriverRows(rows=stored.rows).rows if stored and stored.rows else []


def save_driver_rows(db: Session, rows: list[DriverRow], editor: str) -> None:
    stored = db.get(EeeTaxiDriverMaster, 1)
    if stored is None:
        stored = EeeTaxiDriverMaster(id=1)
        db.add(stored)
    stored.rows = [row.model_dump() for row in rows]
    stored.updated_by = editor
    stored.updated_at = datetime.utcnow()
    db.commit()
