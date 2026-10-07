"""EEE-Taxi invoice batch API endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from loguru import logger

from app.config import settings
from app.database import SessionLocal
from app.services.auth import require_permission
from app.models import EeeTaxiBatch, EeeTaxiBatchStatus, EeeTaxiInvoice, EeeTaxiInvoiceStatus, User
from app.services.eee_taxi_csv import financial_year, format_invoice_no, parse_eee_taxi_csv
from app.services.eee_taxi_fare_check import STATUS_OK, apply_card_fares, check_p2p_fares
from app.services.eee_taxi_pipeline import (
    build_zip,
    finalize_batch_status,
    generate_next_invoice,
    has_signed_pdf,
    signed_filename,
    signed_pdf_bytes,
    unsigned_pdf_bytes,
)
from app.services.eee_taxi_rates import get_rate_card, rate_card_to_dict
from app.services.eee_taxi_profiles import rates_for_client, parse_trips, snapshot_rates, invoice_number, client_master_for, detect_client_profile
from app.services.eee_taxi_client_master import snapshot_client_master
from app.services.ey_rates import EyRateCard
from app.services.ey_fares import generate_ey_calc_csv, parse_ey_overrides, apply_ey_fares
from app.services.trip_upload import trip_upload_as_csv
from app.services.eee_taxi_review import review_rows
from app.services.eee_taxi_rental_calc import (
    apply_fare_overrides,
    generate_full_calc_csv,
    parse_calc_csv,
)

router = APIRouter(
    prefix="/api/eee-taxi",
    tags=["eee-taxi"],
    dependencies=[Depends(require_permission("eee_taxi"))],
)


# ── Calculate fares for all rows ─────────────────────────────────────────────

@router.post("/calculate")
async def calculate_fares(csv_file: UploadFile):
    """Parse CSV, calculate all fares (P2P + rental), return enriched CSV.

    Response headers carry row counts:
        X-Total-Count, X-P2P-Count, X-Rental-Count
    User downloads the CSV, optionally edits Calc_Trip_Fare, and re-uploads
    that file when starting the batch to override any calculated fare.
    """
    try:
        csv_bytes = trip_upload_as_csv(await csv_file.read(), csv_file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        client_profile = detect_client_profile(csv_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with SessionLocal() as db:
        rates = rates_for_client(db, client_profile)
        client_master = client_master_for(db, client_profile)
    try:
        original_headers, rows = parse_trips(csv_bytes, rates, client_master)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400, detail="No valid data rows found in CSV.")

    try:
        calc_bytes = (generate_ey_calc_csv(rows, original_headers, rates) if client_profile == "ey"
                      else generate_full_calc_csv(rows, original_headers, rates))
    except Exception as exc:
        logger.exception("generate_full_calc_csv failed")
        raise HTTPException(status_code=500, detail=f"Fare calculation failed: {exc}") from exc
    p2p_count    = sum(1 for r in rows if r.booking_type == "p2p")
    rental_count = sum(1 for r in rows if r.booking_type == "rental")
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    base     = (csv_file.filename or "batch").rsplit(".", 1)[0]
    filename = f"calculated_{base}_{ts}.csv"
    return Response(
        content=calc_bytes,
        media_type="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Count":  str(len(rows)),
            "X-P2P-Count":    str(p2p_count),
            "X-Rental-Count": str(rental_count),
            "X-Client-Profile": client_profile,
            "Access-Control-Expose-Headers": "X-Total-Count, X-P2P-Count, X-Rental-Count, X-Client-Profile",
        },
    )


# ── P2P fare check ───────────────────────────────────────────────────────────

@router.post("/fare-check")
async def fare_check(csv_file: UploadFile):
    """List P2P trips whose CSV fare differs from the rate card or match no route."""
    try:
        csv_bytes = trip_upload_as_csv(await csv_file.read(), csv_file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with SessionLocal() as db:
        rates = get_rate_card(db)
        client_master = client_master_for(db, "pwc")
    try:
        _, rows = parse_trips(csv_bytes, rates, client_master)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    checks = check_p2p_fares(rows, rates)
    return {
        "p2p_count": len(checks),
        "ok_count": sum(1 for c in checks if c.status == STATUS_OK),
        "issues": [c.to_dict() for c in checks if c.status != STATUS_OK],
    }


def _parse_row_list(value: str) -> set[int]:
    try:
        return {int(v) for v in value.split(",") if v.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="use_card_fare_rows must be a comma-separated list of row numbers.") from exc


# ── Review + start batch ──────────────────────────────────────────────────────

async def _prepared_rows(csv_bytes: bytes, calc_csv: Optional[UploadFile], use_card_fare_rows: str,
                         rates, client_master) -> tuple[list, Optional[bytes], set[int]]:
    """Parsed rows with card fares and re-uploaded Calc_Trip_Fare edits applied."""
    try:
        _, rows = parse_trips(csv_bytes, rates, client_master)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}") from exc
    if not rows:
        raise HTTPException(status_code=400, detail="No valid data rows found in CSV.")

    if isinstance(rates, EyRateCard):
        if use_card_fare_rows.strip():
            raise HTTPException(400, "PWC route-card overrides cannot be applied to EY.")
        calc_bytes = await calc_csv.read() if calc_csv is not None else None
        try:
            overrides = parse_ey_overrides(calc_bytes, rows) if calc_bytes else {}
            return apply_ey_fares(rows, rates, overrides), calc_bytes, set()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    overrides: dict = {}
    calc_bytes: bytes | None = None
    if calc_csv is not None:
        raw = await calc_csv.read()
        if raw.strip():
            calc_bytes = raw
            overrides = parse_calc_csv(calc_bytes)
            logger.info("Fare overrides from calculated CSV: {} rows", len(overrides))

    # Rows the user chose to bill at the rate-card route fare; explicit
    # Calc_Trip_Fare edits from a re-uploaded CSV still win over this.
    card_rows = _parse_row_list(use_card_fare_rows)
    if card_rows:
        rows = apply_card_fares(rows, card_rows, rates)
        logger.info("Rate-card fare applied to {} P2P rows", len(card_rows))
    return apply_fare_overrides(rows, overrides, rates), calc_bytes, card_rows


@router.post("/preview")
async def preview_batch(
    csv_file: UploadFile,
    calc_csv: Optional[UploadFile] = File(None),
    use_card_fare_rows: str = Form(""),
):
    """Fare, toll, GST and total per trip, with warnings, before any invoice number is given."""
    try:
        csv_bytes = trip_upload_as_csv(await csv_file.read(), csv_file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        client_profile = detect_client_profile(csv_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with SessionLocal() as db:
        rates = rates_for_client(db, client_profile)
        client_master = client_master_for(db, client_profile)
        rows, _, _ = await _prepared_rows(csv_bytes, calc_csv, use_card_fare_rows, rates, client_master)
        return {"rows": [r.to_dict() for r in review_rows(db, rows, client_master, client_profile)]}


def _taken_numbers(db, numbers: list[str]) -> list[str]:
    taken = db.query(EeeTaxiInvoice.invoice_no).filter(EeeTaxiInvoice.invoice_no.in_(numbers)).all()
    return sorted(n for (n,) in taken)


@router.post("/batch")
async def start_batch(
    csv_file: UploadFile,
    invoice_date: str = Form(...),
    start_suffix: int = Form(..., ge=1, le=9999),
    sign_mode: str = Form("usb"),
    calc_csv: Optional[UploadFile] = File(None),
    use_card_fare_rows: str = Form(""),
    exclude_rows: str = Form(""),
    user: User = Depends(require_permission("eee_taxi")),
):
    """Create a batch from the trips kept on the review screen.

    Removed trips (exclude_rows) get no invoice, and the kept ones are numbered
    one after another from start_suffix, so there is no gap. This only records
    the batch; the browser then calls generate-next once per invoice, because
    work started in a background thread stops as soon as a serverless instance
    is suspended. USB signing happens in the browser through the local signing
    helper, so the DSC PIN is never sent to this server.
    """
    if sign_mode not in ("usb", "dummy"):
        raise HTTPException(status_code=400, detail=f"Invalid sign_mode: {sign_mode!r}.")
    try:
        inv_date = date.fromisoformat(invoice_date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid invoice_date: {invoice_date!r}")

    try:
        csv_bytes = trip_upload_as_csv(await csv_file.read(), csv_file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        client_profile = detect_client_profile(csv_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with SessionLocal() as db:
        rates = rates_for_client(db, client_profile)   # one snapshot for the whole batch
        client_master = client_master_for(db, client_profile)
        rows, calc_bytes, card_rows = await _prepared_rows(csv_bytes, calc_csv, use_card_fare_rows, rates, client_master)
        excluded = _parse_row_list(exclude_rows)
        rows = [r for r in rows if r.row_index not in excluded]
        if not rows:
            raise HTTPException(status_code=400, detail="Every trip was removed; nothing to invoice.")
        if start_suffix + len(rows) - 1 > 9999:
            raise HTTPException(status_code=400, detail="The invoice range cannot go above suffix 9999.")

        fy = financial_year(inv_date)
        numbers = [invoice_number(fy, start_suffix + i, client_profile) for i in range(len(rows))]
        taken = _taken_numbers(db, numbers)
        if taken:
            raise HTTPException(
                status_code=409,
                detail=(f"These invoice numbers already exist: {', '.join(taken[:10])}"
                        f"{' ...' if len(taken) > 10 else ''}. Use a higher starting number, or ask an "
                        "admin to delete the old invoices on Tally Export first."),
            )

        batch_id = uuid.uuid4().hex
        batch = EeeTaxiBatch(
            id=batch_id,
            client_profile=client_profile,
            invoice_date=inv_date,
            start_suffix=start_suffix,
            total_rows=len(rows),
            csv_filename=csv_file.filename,
            sign_mode=sign_mode,
            created_by=user.full_name or user.email,
            # Stored so any later request can rebuild any row on its own.
            csv_data=csv_bytes,
            calc_csv_data=calc_bytes,
            card_fare_rows=sorted(card_rows),
            rates_snapshot=snapshot_rates(rates),
            client_master_snapshot=snapshot_client_master(client_master),
        )
        db.add(batch)
        db.flush()

        for seq, row in enumerate(rows):
            db.add(EeeTaxiInvoice(
                batch_id=batch_id,
                row_index=row.row_index,
                seq=seq,
                route_no=row.route_no.strip() or None,
                entity_name=row.entity_name,
                client_gstin=row.client_gstin,
                booking_type=row.booking_type,
            ))
        db.commit()

    p2p_count    = sum(1 for r in rows if r.booking_type == "p2p")
    rental_count = sum(1 for r in rows if r.booking_type == "rental")
    logger.info(
        "EEE-Taxi batch {} created by {}: {} rows ({} p2p, {} rental, {} removed), numbers {} .. {}",
        batch_id, user.email, len(rows), p2p_count, rental_count, len(excluded), numbers[0], numbers[-1],
    )
    return {
        "batch_id":     batch_id,
        "total_rows":   len(rows),
        "p2p_count":    p2p_count,
        "rental_count": rental_count,
        "first_invoice": numbers[0],
        "last_invoice":  numbers[-1],
    }


# ── Generate one invoice ──────────────────────────────────────────────────────

@router.post("/batch/{batch_id}/generate-next")
def generate_next(batch_id: str):
    """Generate the next pending invoice of a batch.

    The browser calls this repeatedly until ``remaining`` is 0. One invoice per
    request keeps every call short, so there is no serverless timeout and no
    reliance on work continuing after the response is sent.
    """
    with SessionLocal() as db:
        try:
            return generate_next_invoice(batch_id, db)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Batch status ──────────────────────────────────────────────────────────────

@router.get("/batch/{batch_id}")
def get_batch(batch_id: str):
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found.")

        invoices = (
            db.query(EeeTaxiInvoice)
            .filter(EeeTaxiInvoice.batch_id == batch_id)
            .order_by(EeeTaxiInvoice.row_index)
            .all()
        )

        done     = sum(1 for i in invoices if i.status == EeeTaxiInvoiceStatus.DONE)
        failed   = sum(1 for i in invoices if i.status == EeeTaxiInvoiceStatus.FAILED)
        awaiting = sum(1 for i in invoices if i.status == EeeTaxiInvoiceStatus.AWAITING_SIGNATURE)
        p2p_done    = sum(1 for i in invoices if i.status == EeeTaxiInvoiceStatus.DONE and i.booking_type == "p2p")
        rental_done = sum(1 for i in invoices if i.status == EeeTaxiInvoiceStatus.DONE and i.booking_type == "rental")

        return {
            "batch_id":    batch_id,
            "client_profile": batch.client_profile,
            "status":      batch.status,
            "sign_mode":   batch.sign_mode or "usb",
            "total_rows":  batch.total_rows,
            "done":        done,
            "failed":      failed,
            "awaiting":    awaiting,
            "p2p_done":    p2p_done,
            "rental_done": rental_done,
            "invoices": [
                {
                    "id":             inv.id,
                    "row_index":      inv.row_index,
                    "route_no":       inv.route_no,
                    "invoice_no":     inv.invoice_no,
                    "entity_name":    inv.entity_name,
                    "client_gstin":   inv.client_gstin,
                    "booking_type":   inv.booking_type,
                    "status":         inv.status,
                    "error_message":  inv.error_message,
                    "sig_box":        inv.sig_box,
                    "has_signed_pdf": has_signed_pdf(inv),
                }
                for inv in invoices
            ],
        }


# ── Browser-side USB signing ──────────────────────────────────────────────────
#
# The browser fetches the unsigned PDF, posts it to PalliaSignHelper.exe on
# the user's PC (http://127.0.0.1:7777/sign) together with the PIN and the
# signature box, then uploads the signed PDF here.

def _get_batch_invoice(db, batch_id: str, invoice_id: str) -> EeeTaxiInvoice:
    inv = db.get(EeeTaxiInvoice, invoice_id)
    if inv is None or inv.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return inv


@router.get("/batch/{batch_id}/invoice/{invoice_id}/unsigned")
def get_unsigned_invoice(batch_id: str, invoice_id: str):
    """Unsigned PDF for the local signing helper (USB mode only)."""
    with SessionLocal() as db:
        inv = _get_batch_invoice(db, batch_id, invoice_id)
        if inv.status != EeeTaxiInvoiceStatus.AWAITING_SIGNATURE:
            raise HTTPException(status_code=409, detail=f"Invoice is not awaiting signature (status: {inv.status}).")
        data = unsigned_pdf_bytes(inv)
        if not data:
            raise HTTPException(status_code=404, detail="Unsigned PDF is missing.")
        name = Path(inv.pdf_path).name if inv.pdf_path else f"{invoice_id}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.post("/batch/{batch_id}/invoice/{invoice_id}/signed")
async def upload_signed_invoice(batch_id: str, invoice_id: str, file: UploadFile):
    """Store the PDF signed by the local helper and mark the invoice done."""
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF.")

    with SessionLocal() as db:
        inv = _get_batch_invoice(db, batch_id, invoice_id)
        if inv.status == EeeTaxiInvoiceStatus.DONE:
            raise HTTPException(status_code=409, detail="Invoice is already signed.")
        if inv.status != EeeTaxiInvoiceStatus.AWAITING_SIGNATURE:
            raise HTTPException(status_code=409, detail=f"Invoice is not awaiting signature (status: {inv.status}).")

        inv.signed_pdf_data = data
        # The unsigned copy has served its purpose and is never handed out
        # again; dropping it halves what this invoice costs in the database.
        inv.pdf_data = None
        if inv.pdf_path:
            signed_path = Path(inv.pdf_path).with_name(f"{Path(inv.pdf_path).stem}_signed.pdf")
            inv.signed_pdf_path = str(signed_path)
            try:   # best effort; the DB copy is authoritative
                signed_path.parent.mkdir(parents=True, exist_ok=True)
                signed_path.write_bytes(data)
            except OSError as exc:
                logger.warning("Could not write signed PDF to disk ({}); DB copy kept.", exc)
        inv.status        = EeeTaxiInvoiceStatus.DONE
        inv.error_message = None
        db.commit()

        batch = db.get(EeeTaxiBatch, batch_id)
        status = finalize_batch_status(batch, db) if batch else None
        logger.info("Batch {}: invoice {} signed via local helper; batch status={}", batch_id, inv.invoice_no, status)
        return {"id": inv.id, "status": inv.status, "batch_status": status}


@router.post("/batch/{batch_id}/invoice/{invoice_id}/sign-failed")
def report_sign_failure(batch_id: str, invoice_id: str, error: str = Form(...)):
    """Record a per-invoice signing failure reported by the browser."""
    with SessionLocal() as db:
        inv = _get_batch_invoice(db, batch_id, invoice_id)
        if inv.status != EeeTaxiInvoiceStatus.AWAITING_SIGNATURE:
            raise HTTPException(status_code=409, detail=f"Invoice is not awaiting signature (status: {inv.status}).")
        inv.status        = EeeTaxiInvoiceStatus.FAILED
        inv.error_message = error[:1000]
        db.commit()
        batch = db.get(EeeTaxiBatch, batch_id)
        status = finalize_batch_status(batch, db) if batch else None
        logger.warning("Batch {}: invoice {} signing failed in browser: {}", batch_id, inv.invoice_no, error)
        return {"id": inv.id, "status": inv.status, "batch_status": status}


# ── Download single invoice ───────────────────────────────────────────────────

@router.get("/batch/{batch_id}/invoice/{invoice_id}/download")
def download_invoice(batch_id: str, invoice_id: str):
    with SessionLocal() as db:
        inv = _get_batch_invoice(db, batch_id, invoice_id)
        if inv.status != EeeTaxiInvoiceStatus.DONE:
            raise HTTPException(status_code=409, detail="Signed PDF not yet available.")
        data = signed_pdf_bytes(inv)
        if not data:
            raise HTTPException(status_code=404, detail="Signed PDF is missing.")
        name = signed_filename(inv)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── Download ZIPs ─────────────────────────────────────────────────────────────

_DOWNLOADABLE = (
    EeeTaxiBatchStatus.COMPLETED,
    EeeTaxiBatchStatus.PARTIAL,
    EeeTaxiBatchStatus.AWAITING_SIGNATURE,   # ZIP holds whatever is signed so far
)

@router.get("/batch/{batch_id}/download-all")
def download_all(batch_id: str):
    """ZIP of all signed PDFs (both P2P and Rental)."""
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if batch.status not in _DOWNLOADABLE:
            raise HTTPException(status_code=409, detail="Batch not yet completed.")
        zip_bytes = build_zip(batch_id, db)

    filename = f"eee_taxi_all_{batch_id[:8]}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/batch/{batch_id}/download-p2p")
def download_p2p(batch_id: str):
    """ZIP of P2P signed PDFs only."""
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if batch.status not in _DOWNLOADABLE:
            raise HTTPException(status_code=409, detail="Batch not yet completed.")
        zip_bytes = build_zip(batch_id, db, booking_type="p2p")

    filename = f"eee_taxi_p2p_{batch_id[:8]}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/batch/{batch_id}/download-rental")
def download_rental(batch_id: str):
    """ZIP of Rental signed PDFs only."""
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if batch.status not in _DOWNLOADABLE:
            raise HTTPException(status_code=409, detail="Batch not yet completed.")
        zip_bytes = build_zip(batch_id, db, booking_type="rental")

    filename = f"eee_taxi_rental_{batch_id[:8]}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
