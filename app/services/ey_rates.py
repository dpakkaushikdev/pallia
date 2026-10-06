"""EY rate master, stored separately from PWC and snapshotted per batch."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models import EeeTaxiRateCard
from app.services.eee_taxi_rates import get_edit_password_hash


class EyPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fare: int = Field(gt=0, le=100000)
    hours: int = Field(gt=0, le=24)
    kms: int = Field(gt=0, le=2000)


class EyRateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p2p_per_minute: Decimal = Field(default=Decimal("1.5"), ge=0, le=1000, decimal_places=2)
    p2p_per_km: Decimal = Field(default=Decimal("21"), ge=0, le=1000, decimal_places=2)
    p2p_grace_minutes: int = Field(default=15, ge=0, le=120)
    p2p_minimum: Decimal = Field(default=Decimal("322"), gt=0, le=100000, decimal_places=2)
    packages: list[EyPackage] = Field(default_factory=lambda: [
        EyPackage(fare=900, hours=4, kms=40),
        EyPackage(fare=1300, hours=6, kms=60),
        EyPackage(fare=1800, hours=8, kms=80),
    ], min_length=3, max_length=3)
    extra_km_rate: Decimal = Field(default=Decimal("14"), ge=0, le=1000, decimal_places=2)
    extra_minute_rate: Decimal = Field(default=Decimal("2"), ge=0, le=1000, decimal_places=2)
    rental_grace_minutes: int = Field(default=15, ge=0, le=120)
    medium_upgrade_minutes: int = Field(default=300, ge=1, le=1440)
    large_upgrade_minutes: int = Field(default=420, ge=1, le=1440)
    large_upgrade_kms: int = Field(default=70, ge=1, le=2000)
    night_charge: Decimal = Field(default=Decimal("250"), ge=0, le=10000, decimal_places=2)
    night_start_hour: int = Field(default=23, ge=0, le=23)
    night_end_hour: int = Field(default=5, ge=0, le=23)
    # Exact names in the user's Haryana Tally company. Company must be set
    # before export; the PDF does not establish the accounting company name.
    tally_company: str = Field(default="", max_length=255)
    tally_registration: str = Field(default="Haryana Registration", min_length=1, max_length=128)
    tally_sales_local: str = Field(default="CAR RENTAL - LOCAL (5%)", min_length=1, max_length=128)
    tally_sales_interstate: str = Field(default="CAR RENTAL - INTERSTATE (5%)", min_length=1, max_length=128)
    tally_toll: str = Field(default="Toll & Parking", min_length=1, max_length=128)
    tally_cgst: str = Field(default="OUTPUT CGST @2.5%", min_length=1, max_length=128)
    tally_sgst: str = Field(default="OUTPUT SGST @2.5%", min_length=1, max_length=128)
    tally_igst: str = Field(default="OUTPUT IGST @5%", min_length=1, max_length=128)

    @model_validator(mode="after")
    def check_packages(self):
        if len({p.fare for p in self.packages}) != len(self.packages):
            raise ValueError("Package fares must be distinct to identify them in the CSV.")
        if any(a.hours >= b.hours or a.kms >= b.kms or a.fare >= b.fare for a, b in zip(self.packages, self.packages[1:])):
            raise ValueError("Packages must increase in hours, kilometres and fare.")
        if not self.packages[0].hours * 60 <= self.medium_upgrade_minutes < self.packages[1].hours * 60:
            raise ValueError("First upgrade must fall between the first and second package hours.")
        if not self.packages[1].hours * 60 <= self.large_upgrade_minutes < self.packages[2].hours * 60:
            raise ValueError("Second upgrade must fall between the second and third package hours.")
        if not self.packages[1].kms <= self.large_upgrade_kms < self.packages[2].kms:
            raise ValueError("Large-package km threshold must fall between the second and third package limits.")
        if self.night_start_hour == self.night_end_hour:
            raise ValueError("Night start and end must differ.")
        for name in type(self).model_fields:
            if name.startswith("tally_"):
                value = getattr(self, name).strip()
                if name != "tally_company" and not value:
                    raise ValueError("Tally ledger and registration names cannot be blank.")
                setattr(self, name, value)
        return self

    @property
    def rental_packages(self) -> frozenset[int]:
        return frozenset(p.fare for p in self.packages)


DEFAULT_EY_RATES = EyRateCard()
EY_RATE_ID = 2  # PWC always uses row 1, including the shared masters password.


def get_ey_rates(db: Session) -> EyRateCard:
    row = db.get(EeeTaxiRateCard, EY_RATE_ID)
    return EyRateCard.model_validate(row.rates) if row and row.rates else EyRateCard()


def ey_rate_response(db: Session) -> dict:
    row = db.get(EeeTaxiRateCard, EY_RATE_ID)
    return {
        "rates": get_ey_rates(db).model_dump(mode="json"),
        "defaults": DEFAULT_EY_RATES.model_dump(mode="json"),
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row and row.updated_at else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


def save_ey_rates(db: Session, rates: EyRateCard, editor: str) -> None:
    row = db.get(EeeTaxiRateCard, EY_RATE_ID)
    if row is None:
        row = EeeTaxiRateCard(id=EY_RATE_ID)
        db.add(row)
    row.rates = rates.model_dump(mode="json")
    row.updated_by = editor
    row.updated_at = datetime.utcnow()
    db.commit()
