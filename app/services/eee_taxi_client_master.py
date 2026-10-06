"""Editable, profile-specific EEE-Taxi buyer entities."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import EeeTaxiClient
from app.services.eee_taxi_clients import CLIENT_MASTER, STATE_CODES, ClientRecord
from app.services.ey_clients import EY_CLIENTS

ClientProfile = Literal["pwc", "ey"]
_DEFAULTS = {
    "pwc": CLIENT_MASTER,
    "ey": EY_CLIENTS,
}


class ClientMasterRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gstin: str = Field(min_length=15, max_length=15)
    entity_name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=600)

    @field_validator("gstin")
    @classmethod
    def valid_gstin(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"\d{2}[A-Z0-9]{13}", value):
            raise ValueError("GSTIN must contain 15 letters and numbers, starting with a state code.")
        if value[:2] not in STATE_CODES:
            raise ValueError(f"GSTIN starts with unknown state code {value[:2]}.")
        return value

    @field_validator("entity_name", "address")
    @classmethod
    def trimmed_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Entity name and address cannot be blank.")
        return value


class ClientMasterRows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[ClientMasterRow] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_gstins(self):
        seen = set()
        for row in self.rows:
            if row.gstin in seen:
                raise ValueError(f"GSTIN {row.gstin} appears more than once.")
            seen.add(row.gstin)
        return self


def defaults_for(profile: ClientProfile) -> dict[str, ClientRecord]:
    return dict(_DEFAULTS[profile])


def get_client_master(db: Session, profile: ClientProfile) -> dict[str, ClientRecord]:
    records = db.scalars(select(EeeTaxiClient).where(
        EeeTaxiClient.client_profile == profile
    ).order_by(EeeTaxiClient.entity_name, EeeTaxiClient.gstin)).all()
    if not records:
        return defaults_for(profile)
    return {r.gstin: ClientRecord(r.entity_name, r.address, r.gstin) for r in records}


def snapshot_client_master(master: dict[str, ClientRecord]) -> dict[str, dict[str, str]]:
    return {gstin: {"gstin": row.gstin, "entity_name": row.entity_name, "address": row.address}
            for gstin, row in master.items()}


def restore_client_master(snapshot: dict | None, profile: ClientProfile) -> dict[str, ClientRecord]:
    if not snapshot:
        return defaults_for(profile)
    return {gstin: ClientRecord(
        entity_name=values["entity_name"], address=values["address"], gstin=values.get("gstin", gstin),
    ) for gstin, values in snapshot.items()}


def save_client_master(db: Session, profile: ClientProfile, rows: list[ClientMasterRow], updated_by: str) -> None:
    from datetime import datetime
    now = datetime.utcnow()
    db.execute(delete(EeeTaxiClient).where(EeeTaxiClient.client_profile == profile))
    db.add_all(EeeTaxiClient(
        client_profile=profile, gstin=row.gstin, entity_name=row.entity_name,
        address=row.address, updated_by=updated_by, updated_at=now,
    ) for row in rows)
    db.commit()


def client_master_response(db: Session, profile: ClientProfile) -> dict:
    state = get_client_master(db, profile)
    latest = db.scalar(select(EeeTaxiClient).where(
        EeeTaxiClient.client_profile == profile
    ).order_by(EeeTaxiClient.updated_at.desc()).limit(1))
    return {
        "client_profile": profile,
        "rows": [{"gstin": row.gstin, "entity_name": row.entity_name, "address": row.address,
                  "state_code": row.state_code, "state_name": row.state_name}
                 for row in state.values()],
        "updated_by": latest.updated_by if latest else None,
        "updated_at": latest.updated_at.isoformat() + "Z" if latest and latest.updated_at else None,
    }
