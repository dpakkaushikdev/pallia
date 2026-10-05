"""Review before numbering: preview, removed trips, no gaps, no duplicate numbers, admin delete."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app  # conftest.py points DATABASE_URL at a temp DB first
from app.models import EeeTaxiBatch, EeeTaxiCostCentre, EeeTaxiInvoice, User, UserRole
from app.services.auth import get_password_hash
from tests.test_eee_taxi_tally_api import HEADER, P2P_UNMAPPED_CAR, RENTAL

CSV = (HEADER + RENTAL + P2P_UNMAPPED_CAR).encode()


def _clear() -> None:
    with SessionLocal() as db:
        db.query(EeeTaxiInvoice).delete()
        db.query(EeeTaxiBatch).delete()
        db.query(EeeTaxiCostCentre).delete()
        db.commit()


def _login(client: TestClient, email: str, password: str = "Password123") -> dict:
    client.post("/api/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        _clear()
        with SessionLocal() as db:
            db.add(EeeTaxiCostCentre(vehicle_no="HR55BB4906", cost_centre="HR55BB4906"))
            db.commit()
        yield c
        _clear()


@pytest.fixture()
def admin(client):
    headers = _login(client, "admin@test.com")
    with SessionLocal() as db:
        db.query(User).filter(User.email == "admin@test.com").update({User.role: UserRole.ADMIN})
        db.commit()
    return headers


def _files(extra: dict | None = None) -> tuple[dict, dict]:
    data = {"invoice_date": "2026-10-05", "start_suffix": "1388", "sign_mode": "dummy", **(extra or {})}
    return {"csv_file": ("trips.csv", CSV, "text/csv")}, data


def test_preview_shows_breakdown_and_warnings(client, admin):
    resp = client.post("/api/eee-taxi/preview", files={"csv_file": ("trips.csv", CSV, "text/csv")}, headers=admin)
    assert resp.status_code == 200, resp.text
    rental, p2p = resp.json()["rows"]
    assert rental["route_no"] == "GGN-0QRT" and rental["warnings"] == []
    assert float(rental["total"]) == float(rental["fare"]) + float(rental["toll"]) + float(rental["gst"])
    assert any("HR55AX1267 has no Tally cost centre" in w for w in p2p["warnings"])


def test_missing_header_is_named_on_preview(client, admin):
    bad = HEADER.replace("Entity Gst,", "") + RENTAL
    resp = client.post("/api/eee-taxi/preview", files={"csv_file": ("t.csv", bad.encode(), "text/csv")}, headers=admin)
    assert resp.status_code == 400
    assert "Entity Gst" in resp.json()["detail"]


def test_removed_trip_leaves_no_gap_in_numbers(client, admin):
    files, data = _files({"exclude_rows": "0"})
    resp = client.post("/api/eee-taxi/batch", files=files, data=data, headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_rows"] == 1
    assert body["first_invoice"] == body["last_invoice"] == "DL/HO/26-27/1388"

    gen = client.post(f"/api/eee-taxi/batch/{body['batch_id']}/generate-next", headers=admin)
    assert gen.status_code == 200, gen.text
    with SessionLocal() as db:
        [inv] = db.query(EeeTaxiInvoice).all()
        # Row 1 of the CSV, but the first kept trip, so it takes the first number.
        assert (inv.row_index, inv.seq, inv.invoice_no, inv.route_no) == (1, 0, "DL/HO/26-27/1388", "GGN-OYFS")


def test_numbers_already_used_are_refused(client, admin):
    files, data = _files()
    first = client.post("/api/eee-taxi/batch", files=files, data=data, headers=admin).json()
    client.post(f"/api/eee-taxi/batch/{first['batch_id']}/generate-next", headers=admin)

    files, data = _files()
    resp = client.post("/api/eee-taxi/batch", files=files, data=data, headers=admin)
    assert resp.status_code == 409
    assert "DL/HO/26-27/1388" in resp.json()["detail"]


def test_only_admin_can_delete_an_invoice(client, admin):
    files, data = _files({"exclude_rows": "1"})
    batch = client.post("/api/eee-taxi/batch", files=files, data=data, headers=admin).json()
    with SessionLocal() as db:
        [inv] = db.query(EeeTaxiInvoice).all()
        inv_id = inv.id

    with SessionLocal() as db:
        if not db.query(User).filter(User.email == "clerk@test.com").first():
            db.add(User(email="clerk@test.com", hashed_password=get_password_hash("Password123"),
                        role=UserRole.USER, is_active=True, permissions=["eee_taxi"]))
            db.commit()
    regular = _login(client, "clerk@test.com")
    assert client.delete(f"/api/eee-taxi/tally/invoices/{inv_id}", headers=regular).status_code == 403

    assert client.delete(f"/api/eee-taxi/tally/invoices/{inv_id}", headers=admin).status_code == 200
    with SessionLocal() as db:
        assert db.get(EeeTaxiInvoice, inv_id) is None
        assert db.get(EeeTaxiBatch, batch["batch_id"]) is None  # last invoice gone, so the batch goes too
