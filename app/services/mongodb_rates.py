"""MongoDB rate list: one fixed fare per billing zone, edited on the Masters page.

MongoDB's roster report names a Billing Zone for every guest; the trip is
billed at the fare of its costliest zone. Stored in ``eee_taxi_rate_card``
row 3 (PWC uses row 1, EY row 2).
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiRateCard
from app.services.eee_taxi_rates import get_edit_password_hash

MONGODB_RATE_ID = 3
MAX_ZONES = 40


def zone_key(zone: str) -> str:
    """'Gr. Gurgaon' and 'gr gurgaon' are the same zone."""
    return re.sub(r"[^a-z0-9]", "", (zone or "").lower())


class MongoZoneRate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    zone: str = Field(min_length=1, max_length=60)
    fare: Decimal = Field(gt=0, le=100000, decimal_places=2)

    @field_validator("zone")
    @classmethod
    def has_letters(cls, value: str) -> str:
        if not zone_key(value):
            raise ValueError("Zone name needs letters or digits.")
        return value


def _default_zones() -> list[MongoZoneRate]:
    return [MongoZoneRate(zone=zone, fare=Decimal(fare)) for zone, fare in (
        ("Delhi", 1145), ("Gr. Gurgaon", 750), ("Gurgaon", 725),
        ("Noida/Gr. Noida/Ghaziabad", 1445), ("NCR (Ex UP)", 1050),
    )]


class MongoRateList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zones: list[MongoZoneRate] = Field(default_factory=_default_zones, min_length=1, max_length=MAX_ZONES)

    @model_validator(mode="after")
    def no_repeats(self):
        keys = [zone_key(row.zone) for row in self.zones]
        repeated = next((row.zone for row, key in zip(self.zones, keys) if keys.count(key) > 1), None)
        if repeated:
            raise ValueError(f"Zone '{repeated}' is in the list twice.")
        return self

    def fare_for(self, zone: str) -> Decimal | None:
        key = zone_key(zone)
        return next((row.fare for row in self.zones if zone_key(row.zone) == key), None)


def get_mongodb_rates(db: Session) -> MongoRateList:
    row = db.get(EeeTaxiRateCard, MONGODB_RATE_ID)
    return MongoRateList.model_validate(row.rates) if row and row.rates else MongoRateList()


def mongodb_rate_response(db: Session) -> dict:
    row = db.get(EeeTaxiRateCard, MONGODB_RATE_ID)
    return {
        "rates": get_mongodb_rates(db).model_dump(mode="json"),
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row and row.updated_at else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


def save_mongodb_rates(db: Session, rates: MongoRateList, editor: str) -> None:
    row = db.get(EeeTaxiRateCard, MONGODB_RATE_ID)
    if row is None:
        row = EeeTaxiRateCard(id=MONGODB_RATE_ID)
        db.add(row)
    row.rates = rates.model_dump(mode="json")
    row.updated_by = editor
    row.updated_at = datetime.utcnow()
    db.commit()
