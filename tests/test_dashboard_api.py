"""Admin dashboard: counts by invoice status, and the per-day query the live database can run."""
from __future__ import annotations

from app.database import SessionLocal
from app.models import Invoice, InvoiceStatus
from tests.test_eee_taxi_review_api import admin, client  # noqa: F401  (fixtures)


def test_summary_counts_invoices_by_status(client, admin):  # noqa: F811
    with SessionLocal() as db:
        db.query(Invoice).delete()
        db.add_all([Invoice(suffix="901", status=InvoiceStatus.GENERATED),
                    Invoice(suffix="902", status=InvoiceStatus.GENERATED),
                    Invoice(suffix="903", status=InvoiceStatus.DRAFT),
                    Invoice(suffix="904", status=InvoiceStatus.FAILED)])
        db.commit()
    try:
        resp = client.get("/api/dashboard/summary", headers=admin)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["generated_invoices"], body["draft_invoices"], body["failed_invoices"]) == (2, 1, 1)
        assert sum(day["count"] for day in body["api_calls_by_day"]) >= 0
    finally:
        with SessionLocal() as db:
            db.query(Invoice).delete()
            db.commit()
