from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiRateCard, ExternalApiEvent, PalliaVehicleMaster, User, UserRole
from app.services.auth import create_access_token, get_password_hash


def _user_headers(db, role=UserRole.ADMIN, permissions=None):
    email = f"pallia-test-{uuid.uuid4().hex}@example.com"
    user = User(email=email, hashed_password=get_password_hash("Password123"), role=role,
                permissions=permissions if permissions is not None else ["billing"])
    db.add(user)
    db.commit()
    return user, {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


def test_pallia_vehicle_master_keeps_csv_details_and_legacy_strings(monkeypatch):
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.query(PalliaVehicleMaster).delete()
            original_edit_row = db.get(EeeTaxiRateCard, 1)
            original_edit_row_present = original_edit_row is not None
            original_password_hash = original_edit_row.edit_password_hash if original_edit_row else None
            if original_edit_row is None:
                original_edit_row = EeeTaxiRateCard(id=1, rates={}, edit_password_hash=None)
                db.add(original_edit_row)
            original_edit_row.edit_password_hash = get_password_hash("MasterEdit123")
            user, headers = _user_headers(db, role=UserRole.USER, permissions=["tracking"])
            db.commit()
        try:
            assert client.get("/api/pallia/vehicle-master", headers=headers).status_code == 200
            assert client.post("/api/pallia/vehicle-verification", headers=headers,
                               json={"vehicles": ["NL01AA1234"]}).status_code == 403
            assert client.put("/api/pallia/vehicle-master", headers=headers,
                              json={"vehicles": ["DL10CY2147"]}).status_code == 403
            response = client.put("/api/pallia/vehicle-master", headers=headers, json={
                "edit_password": "MasterEdit123",
                "vehicles": [
                    {"exc": "1", "Registration No": "HR55BD8390", "registration_date": "3/30/2021", "Make": "TATA MOTORS LTD", "Model": "TATA SIGNA 4018.S BSVI"},
                    "DL10CY2147",
                ],
            })
            assert response.status_code == 200, response.text
            rows = response.json()["vehicles"]
            assert rows[0] == {"exc": "1", "registration_no": "HR55BD8390", "registration_date": "3/30/2021", "make": "TATA MOTORS LTD", "model": "TATA SIGNA 4018.S BSVI"}
            assert rows[1]["registration_no"] == "DL10CY2147"
            assert client.get("/api/pallia/vehicle-master", headers=headers).json()["vehicles"] == rows

            _, eee_headers = _user_headers(db, role=UserRole.USER, permissions=["eee_taxi"])
            assert client.get("/api/pallia/vehicle-master", headers=eee_headers).status_code == 403
        finally:
            with SessionLocal() as db:
                db.query(PalliaVehicleMaster).delete()
                db.query(User).filter(User.email.like("pallia-test-%@example.com")).delete(synchronize_session=False)
                rate_card = db.get(EeeTaxiRateCard, 1)
                if rate_card:
                    if original_edit_row_present:
                        rate_card.edit_password_hash = original_password_hash
                    else:
                        db.delete(rate_card)
                db.commit()


def test_surepass_verification_logs_each_vehicle_by_user_and_returns_csv(monkeypatch):
    import app.api.pallia_verification as verification

    class FakeResponse:
        status_code = 200
        is_success = True
        text = ""

        @staticmethod
        def json():
            return {"success": True, "data": {"registration_number": "NL01AH6556"}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json):
            assert url.endswith("/rc/rc-v2")
            assert json["enrich"] is False
            return FakeResponse()

    monkeypatch.setattr(verification.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "auth_token_surepass", "test-token")
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.query(ExternalApiEvent).filter(ExternalApiEvent.api_name == "surepass").delete()
            db.query(PalliaVehicleMaster).delete()
            user, headers = _user_headers(db, role=UserRole.USER, permissions=["others"])
            admin, admin_headers = _user_headers(db)
            db.add(PalliaVehicleMaster(id=1, vehicles=["NL01AH6556", "DL10CY2147"]))
            db.commit()
        response = client.post("/api/pallia/vehicle-verification", headers=headers, json={"vehicles": ["NL01AH6556", "DL10CY2147"]})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert "vehicle_number" in response.text and "NL01AH6556" in response.text
        dashboard = client.get("/api/dashboard/external-apis", headers=admin_headers)
        assert dashboard.status_code == 200, dashboard.text
        user_summary = next(row for row in dashboard.json()["by_user"] if row["email"] == user.email)
        assert user_summary["total_calls"] == 2
        assert user_summary["successful"] == 2
        with SessionLocal() as db:
            db.query(ExternalApiEvent).filter(ExternalApiEvent.api_name == "surepass").delete()
            db.query(PalliaVehicleMaster).delete()
            db.query(User).filter(User.id.in_([user.id, admin.id])).delete(synchronize_session=False)
            db.commit()


def test_user_business_scope_filters_permissions_on_create_and_edit():
    with TestClient(app) as client:
        with SessionLocal() as db:
            admin, admin_headers = _user_headers(db)
            db.commit()
        created = client.post("/api/auth/register", headers=admin_headers, json={
            "email": f"scope-test-{uuid.uuid4().hex}@example.com",
            "password": "Password123",
            "role": "user",
            "business": "eee_taxi",
            "permissions": ["billing", "others", "eee_taxi"],
        })
        assert created.status_code == 201, created.text
        user = created.json()
        assert user["business"] == "eee_taxi"
        assert user["permissions"] == ["eee_taxi"]

        rejected = client.patch(f"/api/auth/users/{user['id']}", headers=admin_headers, json={
            "business": "pallia", "permissions": ["eee_taxi"],
        })
        assert rejected.status_code == 400
        changed = client.patch(f"/api/auth/users/{user['id']}", headers=admin_headers, json={
            "business": "pallia", "permissions": ["billing", "eee_taxi"],
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["business"] == "pallia"
        assert changed.json()["permissions"] == ["billing"]
        with SessionLocal() as db:
            db.query(User).filter(User.id == user["id"]).delete()
            db.query(User).filter(User.id == admin.id).delete()
            db.commit()


def test_pallia_api_rates_require_shared_master_password_and_persist():
    with TestClient(app) as client:
        with SessionLocal() as db:
            original_master = db.get(PalliaVehicleMaster, 1)
            original_rates = dict(original_master.api_rates) if original_master and original_master.api_rates else None
            original_rates_updated_by = original_master.api_rates_updated_by if original_master else None
            original_rates_updated_at = original_master.api_rates_updated_at if original_master else None
            original_master_present = original_master is not None
            original_edit_row = db.get(EeeTaxiRateCard, 1)
            original_edit_row_present = original_edit_row is not None
            original_password_hash = original_edit_row.edit_password_hash if original_edit_row else None
            if original_edit_row is None:
                original_edit_row = EeeTaxiRateCard(id=1, rates={}, edit_password_hash=None)
                db.add(original_edit_row)
            original_edit_row.edit_password_hash = get_password_hash("MasterEdit123")
            user, headers = _user_headers(db)
            db.commit()

        try:
            initial = client.get("/api/pallia/api-rates", headers=headers)
            assert initial.status_code == 200
            assert initial.json()["rates"]["rc_verification"] == 4
            assert initial.json()["rates"]["aadhaar_pan_link"] is None
            assert client.post("/api/pallia/api-rates/unlock", headers=headers, json={"password": "wrong"}).status_code == 403
            assert client.post("/api/pallia/api-rates/unlock", headers=headers, json={"password": "MasterEdit123"}).status_code == 200
            values = {"rc_verification": 4.5, "aadhaar_pan_link": None, "bank_verification": 12,
                      "company_details": None, "corporate_cin": 8, "corporate_gstin": 6}
            assert client.put("/api/pallia/api-rates", headers=headers,
                              json={"edit_password": "wrong", "rates": values}).status_code == 403
            saved = client.put("/api/pallia/api-rates", headers=headers,
                               json={"edit_password": "MasterEdit123", "rates": values})
            assert saved.status_code == 200, saved.text
            assert saved.json()["rates"] == values
            assert client.get("/api/pallia/api-rates", headers=headers).json()["rates"] == values
        finally:
            with SessionLocal() as db:
                row = db.get(PalliaVehicleMaster, 1)
                if original_master_present:
                    row.api_rates = original_rates
                    row.api_rates_updated_by = original_rates_updated_by
                    row.api_rates_updated_at = original_rates_updated_at
                elif row is not None:
                    db.delete(row)
                rate_card = db.get(EeeTaxiRateCard, 1)
                if rate_card:
                    if not original_edit_row_present:
                        db.delete(rate_card)
                    else:
                        rate_card.edit_password_hash = original_password_hash
                db.query(User).filter(User.id == user.id).delete()
                db.commit()
