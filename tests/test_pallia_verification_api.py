from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import ExternalApiEvent, PalliaVehicleMaster, User, UserRole
from app.services.auth import create_access_token, get_password_hash


def _user_headers(db, role=UserRole.ADMIN):
    email = f"pallia-test-{uuid.uuid4().hex}@example.com"
    user = User(email=email, hashed_password=get_password_hash("Password123"), role=role, permissions=["billing"])
    db.add(user)
    db.commit()
    return user, {"Authorization": f"Bearer {create_access_token({'sub': email})}"}


def test_pallia_vehicle_master_keeps_csv_details_and_legacy_strings(monkeypatch):
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.query(PalliaVehicleMaster).delete()
            user, headers = _user_headers(db)
            db.commit()
        response = client.put("/api/pallia/vehicle-master", headers=headers, json={"vehicles": [
            {"exc": "1", "Registration No": "HR55BD8390", "registration_date": "3/30/2021", "Make": "TATA MOTORS LTD", "Model": "TATA SIGNA 4018.S BSVI"},
            "DL10CY2147",
        ]})
        assert response.status_code == 200, response.text
        rows = response.json()["vehicles"]
        assert rows[0] == {"exc": "1", "registration_no": "HR55BD8390", "registration_date": "3/30/2021", "make": "TATA MOTORS LTD", "model": "TATA SIGNA 4018.S BSVI"}
        assert rows[1]["registration_no"] == "DL10CY2147"
        assert client.get("/api/pallia/vehicle-master", headers=headers).json()["vehicles"] == rows
        with SessionLocal() as db:
            db.query(PalliaVehicleMaster).delete()
            db.query(User).filter(User.id == user.id).delete()
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
            user, headers = _user_headers(db)
            db.add(PalliaVehicleMaster(id=1, vehicles=["NL01AH6556", "DL10CY2147"]))
            db.commit()
        response = client.post("/api/pallia/vehicle-verification", headers=headers, json={"vehicles": ["NL01AH6556", "DL10CY2147"]})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert "vehicle_number" in response.text and "NL01AH6556" in response.text
        dashboard = client.get("/api/dashboard/external-apis", headers=headers)
        assert dashboard.status_code == 200, dashboard.text
        user_summary = next(row for row in dashboard.json()["by_user"] if row["email"] == user.email)
        assert user_summary["total_calls"] == 2
        assert user_summary["successful"] == 2
        with SessionLocal() as db:
            db.query(ExternalApiEvent).filter(ExternalApiEvent.api_name == "surepass").delete()
            db.query(PalliaVehicleMaster).delete()
            db.query(User).filter(User.id == user.id).delete()
            db.commit()
