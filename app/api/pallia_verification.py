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
SUREPASS_BANK_URL = "https://kyc-api.surepass.app/api/v1/bank-verification"
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
    vehicles: list[str] = Field(min_length=1, max_length=3000)

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


class BankAccountIn(BaseModel):
    id_number: str = Field(default="", max_length=128)
    ifsc: str = Field(default="", max_length=32)

    @field_validator("id_number", "ifsc")
    @classmethod
    def trim_values(cls, value):
        return value.strip()

    @field_validator("ifsc")
    @classmethod
    def normalize_ifsc(cls, value):
        return value.upper()


class VerifyBankAccountsIn(BaseModel):
    accounts: list[BankAccountIn] = Field(min_length=1, max_length=3000)


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
            try:
                payload = response.json()
            except ValueError:
                row["response_text"] = response.text[:4000]
                row["error"] = "Surepass returned a response that was not valid JSON."
                payload = None
            if isinstance(payload, dict):
                row.update(_flatten(payload))
                row["message"] = str(payload.get("message") or "")
                if not response.is_success:
                    row["error"] = f"Surepass returned HTTP {response.status_code}."
                elif payload.get("success") is False:
                    row["error"] = row["message"] or "Surepass reported that this verification failed."
            elif payload is not None:
                row["response"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if not response.is_success:
                    row["error"] = f"Surepass returned HTTP {response.status_code}."
            elif not response.is_success:
                row["error"] = f"Surepass returned HTTP {response.status_code}."
            elif not row["error"]:
                row["error"] = "Surepass returned an empty JSON response."
            if not response.is_success and not row["message"]:
                row["message"] = f"HTTP {response.status_code} response from Surepass."
            succeeded = response.is_success and payload is not None and not (
                isinstance(payload, dict) and payload.get("success") is False
            ) and not row["error"]
            row["success"] = "true" if succeeded else "false"
            event["success"] = succeeded
            if not succeeded:
                event["error_message"] = (row["error"] or row["message"] or "Surepass verification failed.")[:500]
        except httpx.TimeoutException:
            row["error"] = "Surepass request timed out."
            event["error_message"] = row["error"]
        except httpx.RequestError:
            row["error"] = "Could not connect to Surepass."
            event["error_message"] = row["error"]
        except Exception as exc:
            row["error"] = f"Unexpected verification error ({type(exc).__name__})."
            event["error_message"] = row["error"]
        finally:
            if not row["success"]:
                row["success"] = "false"
            event["duration_ms"] = int((time.monotonic() - started) * 1000)
            row["_api_event"] = event
        return row

    async with httpx.AsyncClient(headers=headers, timeout=45.0) as client:
        rows = await asyncio.gather(*(verify_one(client, vehicle) for vehicle in body.vehicles))

    for row in rows:
        db.add(ExternalApiEvent(**row.pop("_api_event")))
    try:
        db.commit()
    except Exception:
        db.rollback()
        for row in rows:
            row["audit_log_error"] = "Verification completed, but API usage logging failed."

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


@router.post("/bank-verification")
async def verify_bank_accounts(body: VerifyBankAccountsIn, user: User = Depends(verification_user), db: Session = Depends(get_db)):
    if not settings.auth_token_surepass.strip():
        raise HTTPException(503, "Surepass is not configured. Set AUTH_TOKEN_SUREPASS in the server environment.")

    headers = {"Authorization": f"Bearer {settings.auth_token_surepass.strip()}"}
    semaphore = asyncio.Semaphore(8)

    async def verify_one(client, account: BankAccountIn):
        id_number = account.id_number.strip()
        ifsc = account.ifsc.strip().upper()
        include_ifsc_details = bool(ifsc)
        row = {"id_number": id_number, "ifsc": ifsc, "ifsc_details": str(include_ifsc_details).lower(),
               "http_status": "", "success": "false", "message": "", "error": ""}
        if not id_number:
            row["error"] = "ID Number is required; no Surepass request was made."
            return row

        started = time.monotonic()
        event = {"api_name": "surepass", "model": "bank-verification", "success": False,
                 "error_message": None, "duration_ms": None, "user_id": user.id}
        try:
            async with semaphore:
                response = await client.post(
                    SUREPASS_BANK_URL,
                    json={"id_number": id_number, "ifsc": ifsc, "ifsc_details": include_ifsc_details},
                )
            row["http_status"] = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = None
                row["response_text"] = response.text[:4000]
                row["error"] = "Surepass returned a response that was not valid JSON."

            if isinstance(payload, dict):
                row.update(_flatten(payload))
                row["message"] = str(payload.get("message") or "")
                if not response.is_success:
                    row["error"] = f"Surepass returned HTTP {response.status_code}."
                elif payload.get("success") is False:
                    row["error"] = row["message"] or "Surepass reported that this bank verification failed."
            elif payload is not None:
                row["response"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if not response.is_success:
                    row["error"] = f"Surepass returned HTTP {response.status_code}."
            elif not response.is_success:
                row["error"] = f"Surepass returned HTTP {response.status_code}."
            elif not row["error"]:
                row["error"] = "Surepass returned an empty JSON response."

            if not response.is_success and not row["message"]:
                row["message"] = f"HTTP {response.status_code} response from Surepass."
            succeeded = response.is_success and payload is not None and not (
                isinstance(payload, dict) and payload.get("success") is False
            ) and not row["error"]
            row["success"] = "true" if succeeded else "false"
            event["success"] = succeeded
            if not succeeded:
                event["error_message"] = (row["error"] or row["message"] or "Surepass bank verification failed.")[:500]
        except httpx.TimeoutException:
            row["error"] = "Surepass request timed out."
            event["error_message"] = row["error"]
        except httpx.RequestError:
            row["error"] = "Could not connect to Surepass."
            event["error_message"] = row["error"]
        except Exception as exc:
            row["error"] = f"Unexpected verification error ({type(exc).__name__})."
            event["error_message"] = row["error"]
        finally:
            event["duration_ms"] = int((time.monotonic() - started) * 1000)
            row["_api_event"] = event
        return row

    async with httpx.AsyncClient(headers=headers, timeout=45.0) as client:
        rows = await asyncio.gather(*(verify_one(client, account) for account in body.accounts))

    for row in rows:
        event = row.pop("_api_event", None)
        if event:
            db.add(ExternalApiEvent(**event))
    try:
        db.commit()
    except Exception:
        db.rollback()
        for row in rows:
            row["audit_log_error"] = "Verification completed, but API usage logging failed."

    columns = ["id_number", "ifsc", "ifsc_details", "http_status", "success", "message", "error"]
    columns.extend(sorted({key for row in rows for key in row} - set(columns)))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    content = "\ufeff" + output.getvalue()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(content=content.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="pallia_bank_verification_{stamp}.csv"'})
