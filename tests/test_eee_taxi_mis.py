"""MIS tab: MMT upload mapping, master checks, manual edits and Excel export."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiDriverMaster, EeeTaxiMisTrip, EeeTaxiVehicleMaster, User, UserRole
from app.services.auth import get_password_hash
from app.services.eee_taxi_mis import MisTrip, trip_duration
from app.services.eee_taxi_mis_excel import guess_zone

ENDPOINT = "/api/eee-taxi/mis"
DRIVER = {"driver_id": "d1", "name": "TEST DRIVER", "truein_id": "EEED/VK/9001", "mobile": "9000000001"}
VEHICLE = {"vehicle_no": "HR55ZZ0001", "make": "TIGOR-EV"}
MMT_HEADERS = [
    "Trip Date", "Passenger Name", "Vehicle Registration", "Driver Name", "Driver Phone", "Pickup Address",
    "Scheduled Start Date", "Scheduled Start Time", "Actual Start Date", "Actual Start Time", "Destination",
    "Actual End Date", "Actual End Time", "Package", "Status", "Total Distance", "Remarks", "Base Cost",
    "Toll", "Parking", "Total Bill Amount", "Prepaid Amount", "External Id",
]


def _mmt_row(external_id, phone="+919000000001", plate="HR55ZZ0001", package="Airport to City",
             pickup="Terminal 3, IGI Airport", drop="Sector 29, Gurugram 122001"):
    values = ["2026-11-02", "Guest One", plate, "Test Driver", phone, pickup, "2026-11-02", "06:15:00",
              "2026-11-02", "06:04:00", drop, "2026-11-02", "06:36:00", package, "Completed Trip",
              "24", "-", "590.0", "-", "-", "860.0", "860.0", external_id]
    return dict(zip(MMT_HEADERS, values))


def _mmt_file(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Trip Report"
    sheet.append(MMT_HEADERS)
    for row in rows:
        sheet.append([row[h] for h in MMT_HEADERS])
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def _clear():
    with SessionLocal() as db:
        for model in (EeeTaxiMisTrip, EeeTaxiDriverMaster, EeeTaxiVehicleMaster):
            db.query(model).delete()
        db.add(EeeTaxiDriverMaster(id=1, rows=[DRIVER]))
        db.add(EeeTaxiVehicleMaster(id=1, rows=[VEHICLE]))
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        _clear()
        yield c
        with SessionLocal() as db:
            for model in (EeeTaxiMisTrip, EeeTaxiDriverMaster, EeeTaxiVehicleMaster):
                db.query(model).delete()
            db.commit()


@pytest.fixture()
def headers(client):
    with SessionLocal() as db:
        if db.query(User).filter(User.email == "mis@test.com").first() is None:
            db.add(User(email="mis@test.com", hashed_password=get_password_hash("Password123"),
                        role=UserRole.ADMIN, is_active=True))
            db.commit()
    login = client.post("/api/auth/login", json={"email": "mis@test.com", "password": "Password123"})
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def _upload(client, headers, rows):
    files = {"file": ("mmt.xlsx", _mmt_file(rows), "application/octet-stream")}
    return client.post(f"{ENDPOINT}/upload-mmt", files=files, data={"dispatcher_name": "Desk One"}, headers=headers)


def test_mis_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_upload_maps_mmt_columns_and_fills_driver_from_master(client, headers):
    result = _upload(client, headers, [_mmt_row("NC1")])
    assert result.status_code == 200, result.text
    assert result.json() == {"added": 1, "skipped_existing": 0, "need_fixing": 0, "errors": [],
                             "first_date": "2026-11-02", "last_date": "2026-11-02"}
    trip = client.get(ENDPOINT, headers=headers).json()["trips"][0]
    assert trip["date"] == "2026-11-02" and trip["route_no"] == "NC1" and trip["company_name"] == "MMT"
    assert trip["pickup_time"] == "06:15"          # scheduled, not actual start
    assert trip["drop_time"] == "06:36" and trip["trip_duration"] == "00:21"
    assert trip["pickup_zone"] == "Airport" and trip["drop_zone"] == "Gurgaon"
    assert trip["driver_id"] == "EEED/VK/9001" and trip["driver_name"] == "TEST DRIVER"
    assert trip["driver_contact"] == "9000000001" and trip["dispatcher_name"] == "Desk One"
    assert (trip["trip_fare"], trip["mmt_base_cost"], trip["mmt_prepaid_amount"]) == ("860", "590", "860")
    assert trip["toll"] == "" and trip["remarks"] == "" and trip["issues"] == []


def test_reupload_skips_trips_already_in_mis(client, headers):
    _upload(client, headers, [_mmt_row("NC1")])
    again = _upload(client, headers, [_mmt_row("NC1"), _mmt_row("NC2")]).json()
    assert (again["added"], again["skipped_existing"]) == (1, 1)
    assert client.get(ENDPOINT, headers=headers).json()["summary"]["trips"] == 2


def test_unknown_driver_and_cab_are_flagged_not_blocked(client, headers):
    unknown = dict(_mmt_row("NC1", phone="+918000000000", plate="DL01AA0001"), **{"Driver Name": "New Person"})
    result = _upload(client, headers, [unknown]).json()
    assert result["added"] == 1 and result["need_fixing"] == 1
    body = client.get(ENDPOINT, headers=headers).json()
    assert body["trips"][0]["issues"] == ["Driver not in driver master", "Cab DL01AA0001 not in vehicle master"]
    assert body["summary"]["need_fixing"] == 1


def test_driver_with_changed_phone_is_matched_by_unique_name(client, headers):
    _upload(client, headers, [_mmt_row("NC1", phone="+918000000000")])
    trip = client.get(ENDPOINT, headers=headers).json()["trips"][0]
    assert trip["driver_id"] == "EEED/VK/9001" and trip["driver_contact"] == "9000000001" and trip["issues"] == []


def test_bad_rows_are_reported_and_others_imported(client, headers):
    bad = dict(_mmt_row("NC2"), **{"Total Bill Amount": "abc"})
    result = _upload(client, headers, [_mmt_row("NC1"), bad]).json()
    assert result["added"] == 1 and result["errors"] == ["Row 3: Trip Fare: 'abc' is not a number."]


def test_form_errors_name_the_column(client, headers):
    bad = client.post(ENDPOINT, json={"date": "2026-11-02", "company_name": "MMT", "toll": "-5"}, headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"] == "Toll: '-5' is not a valid amount."


def test_non_mmt_file_is_rejected(client, headers):
    files = {"file": ("x.xlsx", b"not a workbook", "application/octet-stream")}
    assert client.post(f"{ENDPOINT}/upload-mmt", files=files, headers=headers).status_code == 400


def test_manual_add_edit_delete(client, headers):
    created = client.post(ENDPOINT, json={"date": "03/11/2026", "company_name": "ey", "driver_id": "EEED/VK/9001",
                                          "cab_no": "hr 55 zz 0001", "pickup_time": "23:30", "drop_time": "00:45",
                                          "start_odometer": "1000", "end_odometer": "1042.5", "trip_fare": "1,200"},
                          headers=headers)
    assert created.status_code == 201, created.text
    trip = created.json()
    assert (trip["date"], trip["company_name"], trip["cab_no"]) == ("2026-11-03", "EY", "HR55ZZ0001")
    assert trip["driver_name"] == "TEST DRIVER" and trip["trip_duration"] == "01:15"
    assert trip["total_kms"] == "42.5" and trip["trip_fare"] == "1200" and trip["issues"] == []
    edited = {k: v for k, v in trip.items() if k in MisTrip.model_fields}
    edited["guest_name"] = "Changed Guest"
    updated = client.put(f"{ENDPOINT}/{trip['id']}", json=edited, headers=headers)
    assert updated.status_code == 200 and updated.json()["guest_name"] == "Changed Guest"
    assert client.post(f"{ENDPOINT}/delete", json={"ids": [trip["id"]]}, headers=headers).json() == {"deleted": 1}
    assert client.get(ENDPOINT, headers=headers).json()["trips"] == []


def test_duplicate_route_no_is_refused(client, headers):
    _upload(client, headers, [_mmt_row("NC1")])
    clash = client.post(ENDPOINT, json={"date": "2026-11-02", "company_name": "MMT", "route_no": "NC1"}, headers=headers)
    assert clash.status_code == 409 and "NC1" in clash.json()["detail"]


def test_filters_summary_and_export(client, headers):
    _upload(client, headers, [_mmt_row("NC1"), dict(_mmt_row("NC2"), **{"Scheduled Start Date": "2026-10-30"})])
    body = client.get(ENDPOINT, params={"date_from": "2026-11-01"}, headers=headers).json()
    assert [t["route_no"] for t in body["trips"]] == ["NC1"] and body["summary"]["trip_fare"] == 860
    assert len(client.get(ENDPOINT, params={"q": "nc2"}, headers=headers).json()["trips"]) == 1
    export = client.get(f"{ENDPOINT}/export", headers=headers)
    assert export.status_code == 200
    sheet = load_workbook(io.BytesIO(export.content)).active
    assert sheet["A1"].value == "Date" and sheet["AL1"].value == "B2C payment Status" and sheet.max_row == 3
    assert sheet["G2"].value == "NC2" and sheet["AB2"].value == 860   # oldest first; Trip Fare is a number


@pytest.mark.parametrize("address, zone", [
    ("Flat 2, Sector 29, Gurugram", "Gurgaon"), ("Noida Sector 62", "Noida"), ("Rohini, New Delhi 110085", "Delhi"),
    ("Delhi Gurgaon Expressway", ""), ("Somewhere unknown", ""),
])
def test_zone_guess(address, zone):
    assert guess_zone(address) == zone


@pytest.mark.parametrize("args, expected", [
    (("2026-11-02", "06:15", "2026-11-02", "06:36"), "00:21"),
    (("2026-11-01", "23:50", "", "00:20"), "00:30"),       # past midnight, no end date
    (("2026-11-02", "06:15", "", ""), ""),
])
def test_trip_duration(args, expected):
    assert trip_duration(*args) == expected


@pytest.mark.parametrize("field, value", [("date", "31/02/2026"), ("pickup_time", "25:00"), ("toll", "-5"),
                                          ("trip_fare", "1e30")])
def test_invalid_values_are_rejected(field, value):
    with pytest.raises(ValueError):
        MisTrip(**{"date": "2026-11-02", "company_name": "MMT", field: value})
