"""Driver master: protected row edits, and the one-time Master.xlsx load."""
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiDriverMaster, EeeTaxiRateCard, User, UserRole
from app.services.auth import get_password_hash
from app.services.eee_taxi_drivers import DriverRows, get_driver_rows
from scripts.import_eee_taxi_master import read_master_workbook

ENDPOINT = "/api/eee-taxi/drivers"
DRIVER = {"driver_id": "abc12345", "name": "TEST DRIVER", "truein_id": "EEED/VK/9999", "mobile": "9000000001"}


def _clear():
    with SessionLocal() as db:
        db.query(EeeTaxiDriverMaster).delete()
        db.query(EeeTaxiRateCard).delete()
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        _clear()
        yield c
        _clear()


def _headers(client):
    with SessionLocal() as db:
        if db.query(User).filter(User.email == "drivers@test.com").first() is None:
            db.add(User(email="drivers@test.com", hashed_password=get_password_hash("Password123"),
                        role=UserRole.ADMIN, is_active=True))
            db.commit()
    login = client.post("/api/auth/login", json={"email": "drivers@test.com", "password": "Password123"})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    client.post("/api/eee-taxi/rates/password", json={"new_password": "DriverEdit1"}, headers=headers)
    return headers


def test_driver_master_requires_login_and_password(client):
    assert client.get(ENDPOINT).status_code == 401
    headers = _headers(client)
    assert client.get(ENDPOINT, headers=headers).json()["rows"] == []
    denied = client.put(ENDPOINT, json={"rows": [DRIVER], "edit_password": "wrong"}, headers=headers)
    assert denied.status_code == 403
    saved = client.put(ENDPOINT, json={"rows": [DRIVER], "edit_password": "DriverEdit1"}, headers=headers)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["rows"][0]["name"] == "TEST DRIVER" and body["rows"][0]["truein_id"] == "EEED/VK/9999"
    assert body["updated_by"] == "drivers@test.com"


def test_add_edit_and_delete_one_driver_at_a_time(client):
    headers = _headers(client)
    client.put(ENDPOINT, json={"rows": [DRIVER], "edit_password": "DriverEdit1"}, headers=headers)
    added = client.put(ENDPOINT, json={"rows": [DRIVER, {"name": "New Driver", "mobile": "9999999999"}],
                                       "edit_password": "DriverEdit1"}, headers=headers).json()["rows"]
    assert len(added) == 2 and added[1]["driver_id"]  # new rows get an id
    edited = [added[0], dict(added[1], mobile="8888888888")]
    rows = client.put(ENDPOINT, json={"rows": edited, "edit_password": "DriverEdit1"}, headers=headers).json()["rows"]
    assert rows[1]["mobile"] == "8888888888" and rows[1]["driver_id"] == added[1]["driver_id"]
    rows = client.put(ENDPOINT, json={"rows": [rows[1]], "edit_password": "DriverEdit1"}, headers=headers).json()["rows"]
    assert [r["name"] for r in rows] == ["New Driver"]


@pytest.mark.parametrize("rows", [
    [DRIVER, dict(DRIVER, name="Other")],          # duplicate driver id
    [dict(DRIVER, name="  ")],                     # blank name
    [dict(DRIVER, aadhar_number="123412341234")],  # sensitive fields are not accepted
])
def test_invalid_driver_rows_are_rejected(rows):
    with pytest.raises(ValueError):
        DriverRows(rows=rows)


def test_empty_master_can_be_saved(client):
    headers = _headers(client)
    client.put(ENDPOINT, json={"rows": [DRIVER], "edit_password": "DriverEdit1"}, headers=headers)
    assert client.put(ENDPOINT, json={"rows": [], "edit_password": "DriverEdit1"}, headers=headers).json()["rows"] == []
    with SessionLocal() as db:
        assert get_driver_rows(db) == []


def _workbook():
    book = Workbook()
    drivers = book.active
    drivers.title = "Driver Master"
    drivers.append(["Driver ID", "Name", "Mobile", "Father Name", "Email ID", "Availability Status", "Status",
                    "Date of Joining", "Branch", "Aadhar Number", "IFSC", "Driver License Expiry Date",
                    "Emergency Contact Name", "Emergency Contact Number", "Emergency Contact Relationship",
                    "Department", "Truein ID", "Alternate Mobile"])
    drivers.append(["abc12345", " TEST DRIVER ", 9000000001, "TEST FATHER", "a@b.com", "Available", "Active",
                    datetime(2026, 3, 9), "Test Branch", 123412341234, "TEST0000001", datetime(2038, 2, 19),
                    "Test Contact", 9000000002, "Parent", "Ad-hoc", "EEED/VK/9999", "NA"])
    drivers.append([None] * 18)
    vehicles = book.create_sheet("Vehicle Master")
    vehicles.append(["Vehicle ID", "Vehicle Number Plate", "Availability", "Pollution Expiry", "Make",
                     "Regn Date", "Chassis no.", "Engine no.", "Customer"])
    vehicles.append(["veh00001", "dl01ab0001", "Available", None, "TIGOR- XR", datetime(2023, 1, 17),
                     "TESTCHASSIS0001", "TESTENGINE0001", "AD-HOC"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_master_workbook_keeps_useful_driver_details_only():
    vehicles, drivers = read_master_workbook(io.BytesIO(_workbook()))
    assert [v.vehicle_no for v in vehicles] == ["DL01AB0001"]
    assert vehicles[0].registration_date == "17/01/2023" and vehicles[0].chassis_no == "TESTCHASSIS0001"
    [driver] = drivers
    assert driver.model_dump() == {
        "driver_id": "abc12345", "name": "TEST DRIVER", "truein_id": "EEED/VK/9999",
        "mobile": "9000000001", "alternate_mobile": "", "father_name": "TEST FATHER", "email": "a@b.com",
        "status": "Active", "availability": "Available", "branch": "Test Branch", "department": "Ad-hoc",
        "date_of_joining": "09/03/2026", "dl_expiry": "19/02/2038", "emergency_contact_name": "Test Contact",
        "emergency_contact_number": "9000000002", "emergency_contact_relationship": "Parent",
    }
