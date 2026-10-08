"""Shared EEE document records; attachments persist with the application's DB."""
from datetime import datetime
from hashlib import sha256
import io
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EeeTaxiDocumentEntry, EeeTaxiDocumentFile, User
from app.services.auth import require_permission

router = APIRouter(prefix="/api/eee-taxi/documents", tags=["eee-taxi-documents"])
eee_user = require_permission("eee_taxi")
MAX_FILE_BYTES = 3 * 1024 * 1024
CATEGORIES = ["invoice", "ds", "parking", "toll_mcd", "gps"]


class EntryIn(BaseModel):
    client_profile: Literal["ey", "pwc"]
    route_no: str = Field(min_length=1, max_length=64)

    @field_validator("route_no", mode="before")
    @classmethod
    def clean_route(cls, value):
        if not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
            raise ValueError("Enter a valid DS no/Route No.")
        return " ".join(value.split()).upper()


def categories(profile):
    return CATEGORIES + (["email_screenshot"] if profile == "ey" else [])


def entry_or_404(db, entry_id):
    entry = db.get(EeeTaxiDocumentEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Document entry not found.")
    return entry


def entry_dict(entry):
    files = [{"id": f.id, "category": f.category, "filename": f.filename,
              "content_type": f.content_type, "size": f.size,
              "uploaded_by": f.uploaded_by, "created_at": f.created_at.isoformat() + "Z"}
             for f in entry.files]
    missing = [cat for cat in categories(entry.client_profile) if not any(f["category"] == cat for f in files)]
    return {"id": entry.id, "client_profile": entry.client_profile, "route_no": entry.route_no,
            "created_by": entry.created_by, "updated_at": entry.updated_at.isoformat() + "Z",
            "categories": categories(entry.client_profile), "files": files, "missing": missing}


@router.get("")
def list_entries(client_profile: Literal["ey", "pwc"] = "ey", search: str = Query("", max_length=64),
                 offset: int = Query(0, ge=0), _: User = Depends(eee_user), db: Session = Depends(get_db)):
    condition = (EeeTaxiDocumentEntry.client_profile == client_profile)
    if search.strip():
        condition &= EeeTaxiDocumentEntry.route_no.contains(search.strip().upper(), autoescape=True)
    total = db.scalar(select(func.count()).select_from(EeeTaxiDocumentEntry).where(condition))
    rows = db.scalars(select(EeeTaxiDocumentEntry).where(condition)
                      .order_by(EeeTaxiDocumentEntry.updated_at.desc(), EeeTaxiDocumentEntry.id)
                      .offset(offset).limit(50)).all()
    return {"entries": [entry_dict(row) for row in rows], "total": total}


@router.post("")
def create_entry(body: EntryIn, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    def existing():
        return db.scalar(select(EeeTaxiDocumentEntry).where(
            EeeTaxiDocumentEntry.client_profile == body.client_profile,
            EeeTaxiDocumentEntry.route_no == body.route_no))
    entry = existing()
    if entry is None:
        entry = EeeTaxiDocumentEntry(**body.model_dump(), created_by=user.email)
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            entry = existing()
            if entry is None:
                raise
    return entry_dict(entry)


@router.get("/{entry_id}")
def read_entry(entry_id: str, _: User = Depends(eee_user), db: Session = Depends(get_db)):
    return entry_dict(entry_or_404(db, entry_id))


def validate_file(data):
    if data.startswith(b"%PDF-"):
        from pypdf import PdfReader
        try:
            if not PdfReader(io.BytesIO(data)).pages:
                raise ValueError("Empty PDF")
        except Exception as exc:
            raise HTTPException(400, "This PDF cannot be read.") from exc
        return "application/pdf", ".pdf"
    try:
        with Image.open(io.BytesIO(data)) as image:
            types = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"),
                     "WEBP": ("image/webp", ".webp")}
            result = types.get(image.format)
            image.verify()
            if result:
                return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        pass
    raise HTTPException(400, "Upload a PDF, PNG, JPG or WebP image.")


@router.post("/{entry_id}/files")
def upload_file(entry_id: str, category: str = Form(...), file: UploadFile = File(...),
                user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    if category not in categories(entry.client_profile):
        raise HTTPException(400, "This document category does not apply to the selected client.")
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Each document must be 3 MB or smaller.")
    content_type, suffix = validate_file(data)
    filename = (file.filename or "screenshot").replace("\\", "/").split("/")[-1]
    filename = "".join(ch for ch in filename if ord(ch) >= 32).strip()[:240] or "document"
    if not filename.lower().endswith(suffix):
        filename += suffix
    digest = sha256(data).hexdigest()
    if not any(f.category == category and f.sha256 == digest for f in entry.files):
        entry.files.append(EeeTaxiDocumentFile(category=category, filename=filename,
            content_type=content_type, size=len(data), sha256=digest, data=data, uploaded_by=user.email))
        entry.updated_at = datetime.utcnow()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # A simultaneous upload of the same attachment is idempotent.
            # Other constraint errors must still surface as failures.
            if not db.scalar(select(EeeTaxiDocumentFile.id).where(
                    EeeTaxiDocumentFile.entry_id == entry_id,
                    EeeTaxiDocumentFile.category == category,
                    EeeTaxiDocumentFile.sha256 == digest)):
                raise
        db.expire(entry, ["files"])
    return entry_dict(entry)


@router.get("/{entry_id}/files/{file_id}")
def download_file(entry_id: str, file_id: str, inline: bool = False,
                  _: User = Depends(eee_user), db: Session = Depends(get_db)):
    file = db.get(EeeTaxiDocumentFile, file_id)
    if file is None or file.entry_id != entry_id:
        raise HTTPException(404, "Document not found.")
    disposition = "inline" if inline else "attachment"
    return Response(file.data, media_type=file.content_type, headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file.filename, safe='')}",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/{entry_id}/files/{file_id}")
def delete_file(entry_id: str, file_id: str, _: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    file = db.get(EeeTaxiDocumentFile, file_id)
    if file is None or file.entry_id != entry_id:
        raise HTTPException(404, "Document not found.")
    db.delete(file)
    entry.updated_at = datetime.utcnow()
    db.commit()
    db.expire(entry, ["files"])
    return entry_dict(entry)
