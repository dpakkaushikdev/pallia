"""Pallia vehicle master and Surepass RC verification endpoints."""
from __future__ import annotations

import csv
import asyncio
import io
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ExternalApiEvent, PalliaVehicleMaster, User
from app.api.eee_taxi_rates import check_edit_password
from app.services.auth import require_any_permission, require_permission
from app.services.eee_taxi_rates import get_edit_password_hash

router = APIRouter(prefix="/api/pallia", tags=["pallia-verification"])
pallia_user = require_any_permission(["billing", "tracking", "accounts", "others"])
verification_user = require_permission("others")
SUREPASS_RC_URL = "https://kyc-api.surepass.app/api/v1/rc/rc-v2"
PALLIA_API_RATE_DEFAULTS = {
    "rc_verification": 4.0,
    "aadhaar_pan_link": None,
    "bank_verification": None,
    "company_details": None,
    "corporate_cin": None,
    "corporate_gstin": None,
}


def normalize_vehicle(value: str) -> str:
    return re.sub(r"[\s-]+", "", value or "").upper()


class VehicleMasterIn(BaseModel):
    vehicles: list[Any] = Field(max_length=3000)
    edit_password: str = Field(default="", max_length=128)

    @field_validator("vehicles")
    @classmethod
    def valid_vehicles(cls, values):
        cleaned = []
        seen = set()
        for raw in values:
            if isinstance(raw, str):
                raw = {"registration_no": raw}
            if not isinstance(raw, dict):
                raise ValueError("Each vehicle must include a registration number.")
            vehicle = normalize_vehicle(raw.get("registration_no") or raw.get("Registration No") or raw.get("vehicle_number") or "")
            if not vehicle or not re.fullmatch(r"[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}", vehicle):
                raise ValueError(f"Invalid vehicle registration: {raw.get('registration_no', raw)}")
            if vehicle in seen:
                raise ValueError(f"Duplicate vehicle registration: {vehicle}")
            seen.add(vehicle)
            cleaned.append({
                "exc": str(raw.get("exc") or raw.get("Exc") or "").strip()[:32],
                "registration_no": vehicle,
                "registration_date": str(raw.get("registration_date") or raw.get("Registration Date") or "").strip(),
                "make": str(raw.get("make") or raw.get("Make") or "").strip()[:128],
                "model": str(raw.get("model") or raw.get("Model") or "").strip()[:128],
            })
        return cleaned


class VerifyVehiclesIn(BaseModel):
    vehicles: list[str] = Field(min_length=1, max_length=3)

    @field_validator("vehicles")
    @classmethod
    def valid_vehicles(cls, values):
        cleaned = []
        seen = set()
        for raw in values:
            vehicle = normalize_vehicle(raw)
            if not vehicle or not re.fullmatch(r"[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}", vehicle):
                raise ValueError(f"Invalid vehicle registration: {raw}")
            if vehicle not in seen:
                seen.add(vehicle)
                cleaned.append(vehicle)
        return cleaned


class PalliaApiRatesIn(BaseModel):
    rc_verification: float | None = Field(default=4, ge=0)
    aadhaar_pan_link: float | None = Field(default=None, ge=0)
    bank_verification: float | None = Field(default=None, ge=0)
    company_details: float | None = Field(default=None, ge=0)
    corporate_cin: float | None = Field(default=None, ge=0)
    corporate_gstin: float | None = Field(default=None, ge=0)


class PalliaApiRatesUpdateIn(BaseModel):
    edit_password: str = Field(min_length=1, max_length=128)
    rates: PalliaApiRatesIn


class PalliaRatesUnlockIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def _master(db: Session):
    stored = db.get(PalliaVehicleMaster, 1)
    vehicles = stored.vehicles if stored else []
    return [
        {"exc": "" if isinstance(v, str) else v.get("exc", ""),
         "registration_no": normalize_vehicle(v if isinstance(v, str) else v.get("registration_no", "")),
         "registration_date": "" if isinstance(v, str) else v.get("registration_date", ""),
         "make": "" if isinstance(v, str) else v.get("make", ""),
         "model": "" if isinstance(v, str) else v.get("model", "")}
        for v in vehicles
    ]


@router.get("/vehicle-master")
def read_vehicle_master(_: User = Depends(pallia_user), db: Session = Depends(get_db)):
    stored = db.get(PalliaVehicleMaster, 1)
    return {
        "vehicles": _master(db),
        "surepass_configured": bool(settings.auth_token_surepass.strip()),
        "updated_by": stored.updated_by if stored else None,
        "updated_at": stored.updated_at.isoformat() + "Z" if stored else None,
    }


@router.put("/vehicle-master")
def save_vehicle_master(body: VehicleMasterIn, user: User = Depends(pallia_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    stored = db.get(PalliaVehicleMaster, 1)
    if stored is None:
        stored = PalliaVehicleMaster(id=1)
        db.add(stored)
    stored.vehicles = body.vehicles
    stored.updated_by = user.email
    stored.updated_at = datetime.utcnow()
    db.commit()
    return read_vehicle_master(user, db)


def _api_rates_response(db: Session) -> dict:
    stored = db.get(PalliaVehicleMaster, 1)
    rates = {**PALLIA_API_RATE_DEFAULTS, **(stored.api_rates or {})} if stored else dict(PALLIA_API_RATE_DEFAULTS)
    return {
        "rates": rates,
        "updated_by": stored.api_rates_updated_by if stored else None,
        "updated_at": stored.api_rates_updated_at.isoformat() + "Z" if stored and stored.api_rates_updated_at else None,
        "has_edit_password": bool(get_edit_password_hash(db)),
    }


@router.get("/api-rates")
def read_pallia_api_rates(_: User = Depends(pallia_user), db: Session = Depends(get_db)):
    return _api_rates_response(db)


@router.post("/api-rates/unlock")
def unlock_pallia_api_rates(body: PalliaRatesUnlockIn, user: User = Depends(pallia_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.password, user)
    return {"ok": True}


@router.put("/api-rates")
def save_pallia_api_rates(body: PalliaApiRatesUpdateIn, user: User = Depends(pallia_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    stored = db.get(PalliaVehicleMaster, 1)
    if stored is None:
        stored = PalliaVehicleMaster(id=1, vehicles=[])
        db.add(stored)
    stored.api_rates = body.rates.model_dump()
    stored.api_rates_updated_by = user.email
    stored.api_rates_updated_at = datetime.utcnow()
    db.commit()
    return _api_rates_response(db)


def _flatten(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(child, name))
    elif isinstance(value, list):
        result[prefix] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        result[prefix] = value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
    return result


@router.post("/vehicle-verification")
async def verify_vehicles(body: VerifyVehiclesIn, user: User = Depends(verification_user), db: Session = Depends(get_db)):
    if not settings.auth_token_surepass.strip():
        raise HTTPException(503, "Surepass is not configured. Set AUTH_TOKEN_SUREPASS in the server environment.")
    allowed = {v["registration_no"] for v in _master(db)}
    outside_master = [vehicle for vehicle in body.vehicles if vehicle not in allowed]
    if outside_master:
        raise HTTPException(400, f"These vehicles are not in Pallia Vehicle Master: {', '.join(outside_master)}")

    headers = {"Authorization": f"Bearer {settings.auth_token_surepass.strip()}"}
    semaphore = asyncio.Semaphore(8)

    async def verify_one(client, vehicle):
        row = {"vehicle_number": vehicle, "http_status": "", "success": "", "message": "", "error": ""}
        started = time.monotonic()
        event = {"api_name": "surepass", "model": "rc-v2", "success": False,
                 "error_message": None, "duration_ms": None, "user_id": user.id}
        try:
            async with semaphore:
                response = await client.post(SUREPASS_RC_URL,
                                             json={"id_number": vehicle, "enrich": False})
            row["http_status"] = response.status_code
            event["success"] = response.is_success
            if not response.is_success:
                event["error_message"] = f"HTTP {response.status_code}"
            try:
                payload = response.json()
            except ValueError:
                payload = {"response_text": response.text[:4000]}
            if isinstance(payload, dict):
                event["success"] = response.is_success and payload.get("success") is not False
                if response.is_success and payload.get("success") is False:
                    event["error_message"] = str(payload.get("message") or "Surepass lookup returned an unsuccessful result")[:500]
                row["success"] = payload.get("success", "")
                row["message"] = payload.get("message", "")
                row.update(_flatten(payload))
            else:
                row["response"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except httpx.TimeoutException:
            row["error"] = "Surepass request timed out."
            event["error_message"] = row["error"]
        except httpx.RequestError:
            row["error"] = "Could not connect to Surepass."
            event["error_message"] = row["error"]
        finally:
            event["duration_ms"] = int((time.monotonic() - started) * 1000)
            row["_api_event"] = event
        return row

    async with httpx.AsyncClient(headers=headers, timeout=45.0) as client:
        rows = await asyncio.gather(*(verify_one(client, vehicle) for vehicle in body.vehicles))

    for row in rows:
        db.add(ExternalApiEvent(**row.pop("_api_event")))
    db.commit()

    columns = ["vehicle_number", "http_status", "success", "message", "error"]
    columns.extend(sorted({key for row in rows for key in row} - set(columns)))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    content = "\ufeff" + output.getvalue()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(content=content.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="pallia_vehicle_verification_{stamp}.csv"'})
