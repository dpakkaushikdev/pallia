"""Excel download of the driver master and the vehicle master."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiDriverMaster, EeeTaxiRateCard, EeeTaxiVehicleMaster, User, UserRole
from app.services.auth import get_password_hash
from app.services.eee_taxi_master_excel import build_master_workbook
from app.services.eee_taxi_vehicles import parse_vehicle_csv

DRIVERS = "/api/eee-taxi/drivers"
VEHICLES = "/api/eee-taxi/vehicles"
PASSWORD = "MasterEdit1"
DRIVER = {"name": "TEST DRIVER", "truein_id": "EEED/VK/9999", "mobile": "09000000001", "status": "Active",
          "dl_expiry": "05/11/2027"}
VEHICLE = {"vehicle_no": "HR55AX3523", "make": "TIGOR-EV", "availability": "Available", "pollution_expiry": "01/02/2027"}


def _clear():
    with SessionLocal() as db:
        for model in (EeeTaxiDriverMaster, EeeTaxiVehicleMaster, EeeTaxiRateCard):
            db.query(model).delete()
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        _clear()
        yield c
        _clear()


@pytest.fixture()
def headers(client):
    with SessionLocal() as db:
        if db.query(User).filter(User.email == "masters@test.com").first() is None:
            db.add(User(email="masters@test.com", hashed_password=get_password_hash("Password123"),
                        role=UserRole.ADMIN, is_active=True))
            db.commit()
    login = client.post("/api/auth/login", json={"email": "masters@test.com", "password": "Password123"})
    found = {"Authorization": "Bearer " + login.json()["access_token"]}
    client.post("/api/eee-taxi/rates/password", json={"new_password": PASSWORD}, headers=found)
    return found


def _rows(content: bytes) -> list[tuple]:
    return list(load_workbook(io.BytesIO(content)).active.iter_rows(values_only=True))


def test_driver_master_downloads_every_driver_with_every_detail(client, headers):
    client.put(DRIVERS, json={"rows": [DRIVER, {"name": "Second Driver"}], "edit_password": PASSWORD}, headers=headers)

    response = client.get(DRIVERS + "/export", headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith('attachment; filename="Driver_Master_')
    header, first, second = _rows(response.content)
    assert header[:3] == ("Name", "Truein ID", "Mobile") and "DL expiry" in header and len(header) == 15
    assert first[:3] == ("TEST DRIVER", "EEED/VK/9999", "09000000001")    # a mobile keeps its leading zero
    assert first[header.index("DL expiry")] == "05/11/2027"
    assert second[0] == "Second Driver" and second[1] is None


def test_vehicle_master_downloads_in_the_columns_the_import_reads(client, headers):
    client.put(VEHICLES, json={"rows": [VEHICLE], "edit_password": PASSWORD}, headers=headers)

    response = client.get(VEHICLES + "/export", headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith('attachment; filename="Vehicle_Master_')
    header, row = _rows(response.content)
    assert header == ("Vehicle Number Plate", "Availability", "Pollution Expiry", "Make", "Regn Date", "Chassis no.",
                      "Engine no.")
    assert row[:4] == ("HR55AX3523", "Available", "01/02/2027", "TIGOR-EV")
    # Saved as CSV, the download goes straight back in through Import vehicle CSV.
    as_csv = "\n".join(",".join(value or "" for value in line) for line in (header, row)).encode()
    assert parse_vehicle_csv(as_csv)[0].vehicle_no == "HR55AX3523"


def test_master_downloads_need_a_login(client):
    assert client.get(DRIVERS + "/export").status_code == 401
    assert client.get(VEHICLES + "/export").status_code == 401


def test_a_value_starting_with_equals_is_kept_as_text_not_run_as_a_formula():
    content = build_master_workbook("Drivers", [("name", "Name")], [{"name": "=1+1"}])

    cell = load_workbook(io.BytesIO(content)).active["A2"]

    assert cell.value == "=1+1" and cell.data_type == "s"
