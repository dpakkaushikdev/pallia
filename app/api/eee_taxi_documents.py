"""Shared EEE document records; attachments persist with the application's DB."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
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
from app.models import EeeTaxiDocumentEntry, EeeTaxiDocumentFile, EeeTaxiDocumentAudit, User
from app.api.eee_taxi_rates import check_edit_password
from app.services.auth import require_permission

router = APIRouter(prefix="/api/eee-taxi/documents", tags=["eee-taxi-documents"])
eee_user = require_permission("eee_taxi")
MAX_FILE_BYTES = 3 * 1024 * 1024
CATEGORIES = ["ds", "parking", "toll_mcd", "gps"]


class EntryIn(BaseModel):
    client_profile: Literal["ey", "pwc"]
    route_no: str = Field(min_length=1, max_length=64)
    document_date: date = Field(default_factory=lambda: datetime.now(ZoneInfo("Asia/Kolkata")).date())

    @field_validator("route_no", mode="before")
    @classmethod
    def clean_route(cls, value):
        if not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
            raise ValueError("Enter a valid DS no/Route No.")
        return " ".join(value.split()).upper()


class PasswordIn(BaseModel):
    edit_password: str = Field(default="", max_length=128)


class EntryUpdate(EntryIn):
    edit_password: str = Field(default="", max_length=128)


class BulkDeleteIn(PasswordIn):
    ids: list[str] = Field(min_length=1, max_length=100)
    used_only: bool = True


def audit(db, entry, action, actor, details=None):
    db.add(EeeTaxiDocumentAudit(entry_id=entry.id, client_profile=entry.client_profile,
        route_no=entry.route_no, action=action, actor=actor, details=details))


def authorize_edit(db, entry, password, user):
    if entry.edit_protected:
        check_edit_password(db, password, user)


def changed(db, entry, user, action, details=None):
    entry.updated_at = datetime.utcnow()
    if entry.edit_protected:
        entry.edited_by = user.email
        entry.edited_at = entry.updated_at
        entry.revision += 1
    entry.is_saved = False
    audit(db, entry, action, user.email, details)


def categories(profile):
    return CATEGORIES + (["email_screenshot"] if profile == "ey" else [])


def entry_or_404(db, entry_id):
    entry = db.scalar(select(EeeTaxiDocumentEntry).where(EeeTaxiDocumentEntry.id == entry_id).with_for_update())
    if entry is None:
        raise HTTPException(404, "Document entry not found.")
    return entry


def entry_dict(entry):
    files = [{"id": f.id, "category": f.category, "filename": f.filename,
              "content_type": f.content_type, "size": f.size,
              "uploaded_by": f.uploaded_by, "created_at": f.created_at.isoformat() + "Z"}
             for f in entry.files]
    entry_categories = categories(entry.client_profile)
    # Keep previously uploaded invoice files visible so users can remove them,
    # while new entries no longer offer Invoice as an upload category.
    if any(f["category"] == "invoice" for f in files):
        entry_categories = ["invoice"] + entry_categories
    missing = [cat for cat in entry_categories if not any(f["category"] == cat for f in files)]
    return {"id": entry.id, "client_profile": entry.client_profile, "route_no": entry.route_no,
            "document_date": entry.document_date.isoformat() if entry.document_date else None,
            "created_by": entry.created_by, "updated_at": entry.updated_at.isoformat() + "Z",
            "categories": entry_categories, "files": files, "missing": missing,
            "is_saved": entry.is_saved, "edit_protected": entry.edit_protected,
            "edited_by": entry.edited_by, "edited_at": entry.edited_at.isoformat() + "Z" if entry.edited_at else None,
            "status": "used" if entry.used_revision >= entry.revision else "ready" if entry.is_saved else "draft",
            "used_at": entry.used_at.isoformat() + "Z" if entry.used_at else None,
            "used_invoice_no": entry.used_invoice_no, "revision": entry.revision}


@router.get("")
def list_entries(client_profile: Literal["ey", "pwc"] | None = None, search: str = Query("", max_length=64),
                 updated_from: date | None = None, updated_to: date | None = None,
                 used: bool | None = None, offset: int = Query(0, ge=0),
                 limit: int = Query(10, ge=1, le=30),
                 _: User = Depends(eee_user), db: Session = Depends(get_db)):
    if updated_from and updated_to and updated_from > updated_to:
        raise HTTPException(400, "From date must be on or before To date.")
    condition = EeeTaxiDocumentEntry.is_saved.is_(True)
    if client_profile:
        condition &= EeeTaxiDocumentEntry.client_profile == client_profile
    if search.strip():
        condition &= EeeTaxiDocumentEntry.route_no.contains(search.strip().upper(), autoescape=True)
    india_offset = timedelta(hours=5, minutes=30)
    if updated_from:
        condition &= EeeTaxiDocumentEntry.updated_at >= datetime.combine(updated_from, time.min) - india_offset
    if updated_to:
        condition &= EeeTaxiDocumentEntry.updated_at < datetime.combine(updated_to + timedelta(days=1), time.min) - india_offset
    used_condition = EeeTaxiDocumentEntry.used_revision >= EeeTaxiDocumentEntry.revision
    if used is not None:
        condition &= used_condition if used else ~used_condition
    total = db.scalar(select(func.count()).select_from(EeeTaxiDocumentEntry).where(condition))
    total_all = db.scalar(select(func.count()).select_from(EeeTaxiDocumentEntry).where(EeeTaxiDocumentEntry.is_saved.is_(True)))
    rows = db.scalars(select(EeeTaxiDocumentEntry).where(condition)
                      .order_by(EeeTaxiDocumentEntry.updated_at.desc(), EeeTaxiDocumentEntry.id)
                      .offset(offset).limit(limit)).all()
    return {"entries": [entry_dict(row) for row in rows], "total": total, "total_all": total_all, "page_size": limit}


@router.post("/bulk-delete")
def bulk_delete(body: BulkDeleteIn, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    check_edit_password(db, body.edit_password, user)
    rows = db.scalars(select(EeeTaxiDocumentEntry).where(EeeTaxiDocumentEntry.id.in_(set(body.ids))).with_for_update()).all()
    if len(rows) != len(set(body.ids)):
        raise HTTPException(404, "An entry was already deleted. Refresh the list.")
    if body.used_only and any(row.used_revision < row.revision for row in rows):
        raise HTTPException(409, "Bulk cleanup can delete only used entries. Refresh the history.")
    freed = sum(file.size for row in rows for file in row.files)
    for row in rows:
        audit(db, row, "deleted", user.email, {"files": len(row.files), "bytes": sum(f.size for f in row.files)})
        db.delete(row)
    db.commit()
    return {"deleted": len(rows), "freed_bytes": freed}


@router.post("")
def create_entry(body: EntryIn, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    def existing():
        return db.scalar(select(EeeTaxiDocumentEntry).where(
            EeeTaxiDocumentEntry.client_profile == body.client_profile,
            EeeTaxiDocumentEntry.route_no == body.route_no))
    entry = existing()
    if entry is not None and entry.is_saved:
        raise HTTPException(409, "Duplicate entry: documents are already saved for this client and DS/Route number. To edit them, open the record in Saved document entries and choose Edit.")
    if entry is None:
        entry = EeeTaxiDocumentEntry(**body.model_dump(), created_by=user.email)
        db.add(entry)
        try:
            db.flush()
            audit(db, entry, "created", user.email)
            db.commit()
        except IntegrityError:
            db.rollback()
            entry = existing()
            if entry is None:
                raise
            if entry.is_saved:
                raise HTTPException(409, "Duplicate entry: documents are already saved for this client and DS/Route number. To edit them, open the record in Saved document entries and choose Edit.")
    return entry_dict(entry)


@router.post("/{entry_id}/cancel")
def cancel_entry(entry_id: str, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    if entry.is_saved or entry.edit_protected:
        raise HTTPException(409, "Saved entries cannot be canceled. Open the entry from Saved document entries to edit it.")
    audit(db, entry, "cancelled", user.email, {"files": len(entry.files), "bytes": sum(f.size for f in entry.files)})
    db.delete(entry)
    db.commit()
    return {"cancelled": entry_id}


@router.post("/{entry_id}/save")
def save_entry(entry_id: str, body: PasswordIn, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    authorize_edit(db, entry, body.edit_password, user)
    if not entry.files:
        raise HTTPException(400, "Attach at least one document before saving.")
    entry.is_saved = True
    entry.edit_protected = True
    entry.updated_at = datetime.utcnow()
    audit(db, entry, "saved", user.email, {"revision": entry.revision})
    db.commit()
    return entry_dict(entry)


@router.put("/{entry_id}")
def update_entry(entry_id: str, body: EntryUpdate, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    authorize_edit(db, entry, body.edit_password, user)
    if any(file.category not in categories(body.client_profile) for file in entry.files):
        raise HTTPException(400, "Remove Email Screenshot before changing this entry to PWC.")
    old = {"client_profile": entry.client_profile, "route_no": entry.route_no,
           "document_date": entry.document_date.isoformat() if entry.document_date else None}
    entry.client_profile, entry.route_no, entry.document_date = body.client_profile, body.route_no, body.document_date
    changed(db, entry, user, "edited", {"previous": old})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An entry already exists for this client and DS/Route number.") from exc
    return entry_dict(entry)


@router.post("/{entry_id}/delete")
def delete_entry(entry_id: str, body: PasswordIn, user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    check_edit_password(db, body.edit_password, user)
    freed = sum(file.size for file in entry.files)
    audit(db, entry, "deleted", user.email, {"files": len(entry.files), "bytes": freed})
    db.delete(entry)
    db.commit()
    return {"deleted": entry_id, "freed_bytes": freed}


@router.get("/{entry_id}/audit")
def read_audit(entry_id: str, _: User = Depends(eee_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(EeeTaxiDocumentAudit).where(EeeTaxiDocumentAudit.entry_id == entry_id)
                      .order_by(EeeTaxiDocumentAudit.created_at.desc())).all()
    return {"events": [{"action": row.action, "actor": row.actor,
                       "date": row.created_at.isoformat() + "Z", "details": row.details} for row in rows]}


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
                edit_password: str = Form(""), user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    authorize_edit(db, entry, edit_password, user)
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
        changed(db, entry, user, "file_added", {"category": category, "filename": filename})
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
def delete_file(entry_id: str, file_id: str, body: PasswordIn = PasswordIn(), user: User = Depends(eee_user), db: Session = Depends(get_db)):
    entry = entry_or_404(db, entry_id)
    authorize_edit(db, entry, body.edit_password, user)
    file = db.get(EeeTaxiDocumentFile, file_id)
    if file is None or file.entry_id != entry_id:
        raise HTTPException(404, "Document not found.")
    db.delete(file)
    changed(db, entry, user, "file_removed", {"filename": file.filename, "category": file.category})
    db.commit()
    db.expire(entry, ["files"])
    return entry_dict(entry)
