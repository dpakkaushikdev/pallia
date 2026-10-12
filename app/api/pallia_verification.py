"""Pallia vehicle master and Surepass RC verification endpoints."""
from __future__ import annotations

import csv
import asyncio
import io
import json
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PalliaVehicleMaster, User
from app.services.auth import require_permission

router = APIRouter(prefix="/api/pallia", tags=["pallia-verification"])
pallia_user = require_permission("billing")
SUREPASS_RC_URL = "https://kyc-api.surepass.app/api/v1/rc/rc-v2"


def normalize_vehicle(value: str) -> str:
    return re.sub(r"[\s-]+", "", value or "").upper()


class VehicleMasterIn(BaseModel):
    vehicles: list[str] = Field(max_length=3000)

    @field_validator("vehicles")
    @classmethod
    def valid_vehicles(cls, values):
        cleaned = []
        seen = set()
        for raw in values:
            vehicle = normalize_vehicle(raw)
            if not vehicle or not re.fullmatch(r"[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}", vehicle):
                raise ValueError(f"Invalid vehicle registration: {raw}")
            if vehicle in seen:
                raise ValueError(f"Duplicate vehicle registration: {vehicle}")
            seen.add(vehicle)
            cleaned.append(vehicle)
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


def _master(db: Session):
    stored = db.get(PalliaVehicleMaster, 1)
    return stored.vehicles if stored else []


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
    stored = db.get(PalliaVehicleMaster, 1)
    if stored is None:
        stored = PalliaVehicleMaster(id=1)
        db.add(stored)
    stored.vehicles = body.vehicles
    stored.updated_by = user.email
    stored.updated_at = datetime.utcnow()
    db.commit()
    return read_vehicle_master(user, db)


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
async def verify_vehicles(body: VerifyVehiclesIn, _: User = Depends(pallia_user), db: Session = Depends(get_db)):
    if not settings.auth_token_surepass.strip():
        raise HTTPException(503, "Surepass is not configured. Set AUTH_TOKEN_SUREPASS in the server environment.")
    allowed = set(_master(db))
    outside_master = [vehicle for vehicle in body.vehicles if vehicle not in allowed]
    if outside_master:
        raise HTTPException(400, f"These vehicles are not in Pallia Vehicle Master: {', '.join(outside_master)}")

    headers = {"Authorization": f"Bearer {settings.auth_token_surepass.strip()}"}
    semaphore = asyncio.Semaphore(8)

    async def verify_one(client, vehicle):
        row = {"vehicle_number": vehicle, "http_status": "", "success": "", "message": "", "error": ""}
        try:
            async with semaphore:
                response = await client.post(SUREPASS_RC_URL,
                                             json={"id_number": vehicle, "enrich": False})
            row["http_status"] = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {"response_text": response.text[:4000]}
            if isinstance(payload, dict):
                row["success"] = payload.get("success", "")
                row["message"] = payload.get("message", "")
                row.update(_flatten(payload))
            else:
                row["response"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except httpx.TimeoutException:
            row["error"] = "Surepass request timed out."
        except httpx.RequestError:
            row["error"] = "Could not connect to Surepass."
        return row

    async with httpx.AsyncClient(headers=headers, timeout=45.0) as client:
        rows = await asyncio.gather(*(verify_one(client, vehicle) for vehicle in body.vehicles))

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
