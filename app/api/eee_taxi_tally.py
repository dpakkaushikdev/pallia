"""EEE-Taxi -> Tally export: preview the invoices, then download the XML.

Only finished (signed) invoices are offered. Downloading records the time on
each invoice so the next export skips it by default; re-exporting is allowed
(ticked "include already exported") because that is how a correction reaches
Tally, and Tally's "Prevent duplicates" stops a second copy of a number.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EeeTaxiBatch, EeeTaxiInvoice, User
from app.services.auth import require_admin, require_permission
from app.services.eee_taxi_tally import ExportItem, delete_invoice, find_export_items, mark_exported, row_total
from app.services.eee_taxi_pipeline import batch_rates, batch_rows
from app.services.tally_export import render_tally_xml
from app.services.ey_rates import get_ey_rates
from app.services.ey_tally import ey_tally_ledgers

router = APIRouter(prefix="/api/eee-taxi/tally", tags=["eee-taxi"])

_eee_user = require_permission("eee_taxi")
_MAX_INVOICES = 2_000
_LOCAL_TZ = ZoneInfo("Asia/Kolkata")


def _local_day_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, _LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


class ExportIn(BaseModel):
    invoice_ids: list[str] = Field(min_length=1, max_length=_MAX_INVOICES)


def _item(i: ExportItem) -> dict:
    return {
        "id": i.invoice_id,
        "client_profile": i.client_profile,
        "invoice_no": i.invoice_no,
        "invoice_date": i.invoice_date.isoformat(),
        "entity_name": i.entity_name,
        "car_no": i.car_no,
        "route_no": i.route_no,
        "booking_type": i.booking_type,
        "created_by": i.created_by,
        "total": f"{i.total:.2f}" if i.total is not None else None,
        "exported_at": i.exported_at.isoformat() + "Z" if i.exported_at else None,
        "problem": i.problem,
    }


@router.get("/preview")
def preview(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    include_exported: bool = Query(False),
    client_profile: Optional[Literal["pwc", "ey"]] = Query(None),
    _: User = Depends(_eee_user),
    db: Session = Depends(get_db),
) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The From date is after the To date.")
    items = find_export_items(db, date_from, date_to, include_exported, newest_first=True, client_profile=client_profile)
    return {"invoices": [_item(i) for i in items]}


@router.get("/history")
def invoice_history(
    client_profile: Optional[Literal["pwc", "ey"]] = Query(None),
    created_from: Optional[date] = Query(None),
    created_to: Optional[date] = Query(None),
    _: User = Depends(_eee_user),
    db: Session = Depends(get_db),
) -> dict:
    """All reserved invoice records, including pending, failed and signed PDFs."""
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The creation From date is after the To date.")
    query = db.query(EeeTaxiInvoice, EeeTaxiBatch).join(
        EeeTaxiBatch, EeeTaxiInvoice.batch_id == EeeTaxiBatch.id
    )
    if client_profile:
        query = query.filter(EeeTaxiBatch.client_profile == client_profile)
    if created_from:
        query = query.filter(EeeTaxiBatch.created_at >= _local_day_utc(created_from))
    if created_to:
        query = query.filter(EeeTaxiBatch.created_at < _local_day_utc(created_to + timedelta(days=1)))
    records = query.order_by(EeeTaxiBatch.created_at.desc(), EeeTaxiInvoice.invoice_no.desc()).all()

    rows_by_batch: dict[str, dict] = {}
    failed_batches: set[str] = set()
    items = []
    for invoice, batch in records:
        if batch.id not in rows_by_batch and batch.id not in failed_batches:
            try:
                rows_by_batch[batch.id] = {
                    row.row_index: row for row in batch_rows(batch, batch_rates(batch))
                } if batch.csv_data else {}
            except Exception as exc:
                logger.warning("Invoice history: batch {} could not be rebuilt: {}", batch.id, exc)
                failed_batches.add(batch.id)
        row = rows_by_batch.get(batch.id, {}).get(invoice.row_index)
        try:
            amount = f"{row_total(row):.2f}" if row is not None else None
        except Exception:
            amount = None
        items.append({
            "id": invoice.id,
            "batch_id": batch.id,
            "client_profile": batch.client_profile or "pwc",
            "invoice_no": invoice.invoice_no,
            "invoice_date": batch.invoice_date.isoformat() if batch.invoice_date else None,
            "created_at": batch.created_at.isoformat() + "Z" if batch.created_at else None,
            "route_no": invoice.route_no or (row.route_no if row else ""),
            "guest_name": row.guest_name if row else "",
            "entity_name": invoice.entity_name or (row.entity_name if row else ""),
            "car_no": row.car_no if row else "",
            "booking_type": invoice.booking_type or (row.booking_type if row else ""),
            "total": amount,
            "status": invoice.status.value if hasattr(invoice.status, "value") else str(invoice.status),
            "batch_status": batch.status.value if hasattr(batch.status, "value") else str(batch.status),
            "created_by": batch.created_by or "",
            "error_message": invoice.error_message or "",
            "exported_at": invoice.tally_exported_at.isoformat() if invoice.tally_exported_at else None,
        })
    return {"invoices": items, "count": len(items)}


@router.post("/export")
def export(body: ExportIn, user: User = Depends(_eee_user), db: Session = Depends(get_db)) -> Response:
    ids = list(dict.fromkeys(body.invoice_ids))
    items = find_export_items(db, include_exported=True, invoice_ids=ids)
    found = {i.invoice_id for i in items}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"{len(missing)} selected invoice(s) are not finished invoices any more. Refresh the list.")
    blocked = [i for i in items if i.problem]
    if blocked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Fix these first: " + "; ".join(f"{i.invoice_no}: {i.problem}" for i in blocked[:10]))

    profiles = {i.client_profile for i in items}
    if len(profiles) != 1:
        raise HTTPException(400, "Export PWC and EY separately; they use different GST registrations and Tally companies.")
    if profiles == {"ey"}:
        try:
            ledgers = ey_tally_ledgers(get_ey_rates(db))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        xml = render_tally_xml([i.voucher for i in items], ledgers)
    else:
        xml = render_tally_xml([i.voucher for i in items])
    mark_exported(db, ids)
    first, last = items[0].invoice_no, items[-1].invoice_no
    logger.info("Tally export by {}: {} invoices ({} .. {})", user.email, len(items), first, last)
    filename = f"tally_{first}_to_{last}.xml".replace("/", "-")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/invoices/{invoice_id}")
def delete(invoice_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Admin only: remove a wrong or duplicate invoice so it never reaches Tally."""
    try:
        inv = delete_invoice(db, invoice_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    logger.warning("EEE-Taxi invoice {} ({}) deleted by {}; exported to Tally: {}",
                   inv.invoice_no, invoice_id, admin.email, inv.tally_exported_at is not None)
    return {"deleted": invoice_id}
