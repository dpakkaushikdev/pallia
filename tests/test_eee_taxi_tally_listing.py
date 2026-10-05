"""Tally export listing: newest first, created-by, amounts and old batches without a CSV."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.database import SessionLocal
from app.main import app  # noqa: F401  (conftest points DATABASE_URL at a temp DB; this creates the tables)
from app.models import EeeTaxiBatch, EeeTaxiCostCentre, EeeTaxiInvoice, EeeTaxiInvoiceStatus
from app.services.eee_taxi_tally import find_export_items
from tests.test_eee_taxi_tally_api import HEADER, P2P_UNMAPPED_CAR, RENTAL


def _clear(db) -> None:
    db.query(EeeTaxiInvoice).delete()
    db.query(EeeTaxiBatch).delete()
    db.query(EeeTaxiCostCentre).delete()
    db.commit()


def _invoice(batch: EeeTaxiBatch, inv_id: str, row_index: int, number: str) -> EeeTaxiInvoice:
    return EeeTaxiInvoice(id=inv_id, batch_id=batch.id, row_index=row_index, invoice_no=number,
                          booking_type="rental", status=EeeTaxiInvoiceStatus.DONE)


@pytest.fixture()
def db():
    from fastapi.testclient import TestClient
    with TestClient(app), SessionLocal() as session:
        _clear(session)
        old = EeeTaxiBatch(invoice_date=date(2026, 9, 29), start_suffix=1, total_rows=1,
                           created_at=datetime(2026, 9, 29, 12, 0))   # made before CSVs were stored
        new = EeeTaxiBatch(invoice_date=date(2026, 10, 5), start_suffix=10, total_rows=2,
                           csv_data=(HEADER + RENTAL + P2P_UNMAPPED_CAR).encode(), created_by="Deepak Kaushik",
                           created_at=datetime(2026, 10, 5, 9, 0))
        session.add_all([old, new])
        session.flush()
        session.add_all([
            _invoice(old, "old", 0, "DL/HO/26-27/0001"),
            _invoice(new, "new-a", 0, "DL/HO/26-27/0010"),
            _invoice(new, "new-b", 1, "DL/HO/26-27/0011"),
        ])
        session.add(EeeTaxiCostCentre(vehicle_no="HR55BB4906", cost_centre="HR55BB4906"))
        session.commit()
        yield session
        _clear(session)


def test_newest_invoice_is_listed_first(db):
    ids = [i.invoice_id for i in find_export_items(db, newest_first=True)]
    assert ids == ["new-b", "new-a", "old"]


def test_export_order_stays_oldest_first(db):
    ids = [i.invoice_id for i in find_export_items(db)]
    assert ids == ["old", "new-a", "new-b"]


def test_created_by_comes_from_the_batch(db):
    items = {i.invoice_id: i for i in find_export_items(db)}
    assert items["new-a"].created_by == "Deepak Kaushik"
    assert items["old"].created_by == ""


def test_blocked_invoice_still_shows_car_and_amount(db):
    blocked = {i.invoice_id: i for i in find_export_items(db)}["new-b"]
    assert "has no cost centre" in blocked.problem
    assert blocked.car_no == "HR55AX1267"
    # P2P fare 1050 + 18% IGST (Haryana client), no toll.
    assert blocked.total == Decimal("1239.00")


def test_batch_without_csv_explains_why(db):
    old = {i.invoice_id: i for i in find_export_items(db)}["old"]
    assert "did not keep the uploaded CSV" in old.problem
    assert old.total is None and old.car_no == ""
