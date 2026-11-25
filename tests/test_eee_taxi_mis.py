"""MIS tab: MMT upload mapping, master checks, manual edits and Excel export."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiDriverMaster, EeeTaxiMisTrip, EeeTaxiVehicleMaster, User, UserRole
from app.services.auth import get_password_hash
from app.services.eee_taxi_drivers import DriverRow
from app.services.eee_taxi_mis import MasterIndex, MisTrip, trip_duration
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
    assert result.json() == {"trips_in_file": 1, "added": 1, "skipped_existing": 0, "repeated_in_file": 0, "errors": [],
                             "dry_run": False, "need_fixing": 0, "drivers_to_fix": 0, "driver_trips": 0, "cabs_to_fix": 0,
                             "cab_trips": 0, "fix_drivers": [], "fix_cabs": [], "first_date": "2026-11-02", "last_date": "2026-11-02"}
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


def test_driver_whose_phone_is_not_the_masters_goes_under_needs_fixing(client, headers):
    _upload(client, headers, [_mmt_row("NC1", phone="+918000000000")])
    trip = client.get(ENDPOINT, headers=headers).json()["trips"][0]
    assert trip["driver_id"] == "" and trip["driver_contact"] == "8000000000"       # the name alone is not enough
    assert trip["issues"] == ["Driver TEST DRIVER is in the master with a different mobile"]


@pytest.mark.parametrize("mmt_name, found", [
    ("test  driver.", True),   # spacing, case and dots ignored
    ("Test", True),            # the master name starts with it
    ("Tes", False),            # too short to trust
    ("Other Person", False),
])
def test_uploaded_driver_needs_the_masters_mobile_and_a_fitting_name(mmt_name, found):
    index = _index(DRIVER)
    trip = index.fill(MisTrip(date="2026-11-02", company_name="MMT", driver_name=mmt_name, driver_contact="9000000001"))
    assert (trip.driver_id == "EEED/VK/9001") is found


def test_name_prefix_shared_by_two_drivers_is_not_guessed():
    one, two = DriverRow(name="IMRAN 1", truein_id="A"), DriverRow(name="IMRAN 2", truein_id="B")
    index = MasterIndex(by_name={"imran1": one, "imran2": two}, all_names=[("imran1", one), ("imran2", two)])
    assert index.find_by_name("Imran") is None


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


def test_dispatcher_opens_and_closer_closes_a_manual_trip(client, headers):
    opened = client.post(ENDPOINT, json={"date": "2026-11-03", "company_name": "amazon", "guest_name": "Guest A",
                                         "dispatcher_name": "Desk One"}, headers=headers).json()
    assert opened["company_name"] == "Amazon" and opened["status"] == "Open" and opened["duty_closed_by"] == ""
    assert [t["id"] for t in client.get(ENDPOINT, params={"view": "open"}, headers=headers).json()["trips"]] == [opened["id"]]
    assert client.get(ENDPOINT, params={"view": "closed"}, headers=headers).json()["trips"] == []
    fields = {k: v for k, v in opened.items() if k in MisTrip.model_fields}
    half = client.put(f"{ENDPOINT}/{opened['id']}", json=dict(fields, trip_fare="900"), headers=headers).json()
    assert half["status"] == "Open" and half["duty_closed_by"] == ""
    closed = client.put(f"{ENDPOINT}/{opened['id']}", json=dict(fields, trip_fare="900", drop_time="10:30",
                                                                total_kms="18"), headers=headers).json()
    assert closed["status"] == "Closed" and closed["duty_closed_by"] == "mis@test.com"
    body = client.get(ENDPOINT, params={"company": "AMAZON", "view": "closed"}, headers=headers).json()
    assert len(body["trips"]) == 1 and body["summary"]["open"] == 0


def test_uploaded_mmt_trips_are_closed_and_not_stamped(client, headers):
    _upload(client, headers, [_mmt_row("NC1"), dict(_mmt_row("NC2"), **{"Total Bill Amount": "-"})])
    trips = {t["route_no"]: t for t in client.get(ENDPOINT, headers=headers).json()["trips"]}
    assert (trips["NC1"]["status"], trips["NC2"]["status"]) == ("Closed", "Open")
    assert trips["NC1"]["duty_closed_by"] == ""


def test_unknown_client_is_refused(client, headers):
    bad = client.post(ENDPOINT, json={"date": "2026-11-03", "company_name": "Acme"}, headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"] == "Company Name: 'Acme' is not in the client list."


def test_mmt_booking_id_is_used_only_once(client, headers):
    _upload(client, headers, [_mmt_row("NC1")])
    clash = client.post(ENDPOINT, json={"date": "2026-11-09", "company_name": "MMT", "route_no": "NC1"}, headers=headers)
    assert clash.status_code == 409 and "NC1" in clash.json()["detail"]


def test_other_clients_reuse_a_route_no_on_other_days_and_times(client, headers):
    trip = {"date": "2026-11-02", "company_name": "Decathlon", "route_no": "9000000009", "pickup_time": "20:30"}
    assert client.post(ENDPOINT, json=trip, headers=headers).status_code == 201
    assert client.post(ENDPOINT, json=dict(trip, date="2026-11-03"), headers=headers).status_code == 201
    assert client.post(ENDPOINT, json=dict(trip, pickup_time="23:00"), headers=headers).status_code == 201
    assert client.post(ENDPOINT, json=trip, headers=headers).status_code == 409   # entered twice


def test_copy_books_the_same_run_on_another_date_as_open_trips(client, headers):
    done = {"date": "2026-11-02", "company_name": "Mongo DB", "route_no": "165761", "guest_name": "Guest A",
            "driver_id": "EEED/VK/9001", "cab_no": "HR55ZZ0001", "pickup_address": "Sector 37, Gurugram",
            "pickup_time": "07:40", "drop_location": "MongoDB office", "drop_zone": "gurgon", "drop_time": "08:30",
            "total_kms": "18", "trip_fare": "725"}
    first = client.post(ENDPOINT, json=done, headers=headers).json()
    assert (first["company_name"], first["pickup_zone"], first["drop_zone"]) == ("MongoDB", "Gurgaon", "Gurgaon")
    body = {"ids": [first["id"]], "date": "2026-11-03"}
    assert client.post(f"{ENDPOINT}/copy", json=body, headers=headers).json() == {"added": 1, "skipped_existing": 0}
    copy = client.get(ENDPOINT, params={"date_from": "2026-11-03"}, headers=headers).json()["trips"][0]
    assert copy["status"] == "Open" and copy["missing"] == ["Trip Fare", "Drop Time", "Total kms"]
    assert (copy["guest_name"], copy["cab_no"], copy["pickup_time"], copy["driver_name"]) == ("Guest A", "HR55ZZ0001", "07:40", "TEST DRIVER")
    assert copy["route_no"] == "" and copy["trip_fare"] == ""    # a booking number is not carried over


def test_total_trip_fare_adds_gst_mcd_parking_and_toll():
    trip = MisTrip(date="2026-11-02", company_name="Dalmia", trip_fare="2152.5", mcd="100", parking="280",
                   hub_out="Vasant Kunj", hub_in="20km hub to hub")
    assert trip.total_trip_fare == "2532.5" and trip.hub_in == "20km hub to hub"
    assert MisTrip(date="2026-11-02", company_name="Dalmia", mcd="100").total_trip_fare == ""


@pytest.mark.parametrize("typed, saved", [
    ("Rastey Cabs", "Rastey Cab"), ("raste cabs ", "Rastey Cab"), ("Mongo DB", "MongoDB"), ("ARC ETC", "ARC ETS"),
    ("TataPlay", "TATA Play"), ("Supmere Gartner", "Supreme Gartner"), ("seahawk gartner", "Seahawk Gartner"),
    ("Decathalon", "Decathlon"), ("maintenance", "Maintenance"),
])
def test_client_spellings_from_the_old_sheets_fold_to_one_name(typed, saved):
    assert MisTrip(date="2026-11-02", company_name=typed).company_name == saved


@pytest.mark.parametrize("typed, saved", [("Ghziabad", "Ghaziabad"), ("DElhi", "Delhi"), ("Aiport", "Airport"),
                                          ("gr gurgaon", "Gr Gurgaon"), ("Disposal", "Disposal")])
def test_zone_spellings_fold_to_one_name(typed, saved):
    assert MisTrip(date="2026-11-02", company_name="EY", pickup_zone=typed).pickup_zone == saved


def test_filters_summary_and_export(client, headers):
    _upload(client, headers, [_mmt_row("NC1"), dict(_mmt_row("NC2"), **{"Scheduled Start Date": "2026-10-30"})])
    body = client.get(ENDPOINT, params={"date_from": "2026-11-01"}, headers=headers).json()
    assert [t["route_no"] for t in body["trips"]] == ["NC1"] and body["summary"]["trip_fare"] == 860
    assert len(client.get(ENDPOINT, params={"q": "nc2"}, headers=headers).json()["trips"]) == 1
    export = client.get(f"{ENDPOINT}/export", headers=headers)
    assert export.status_code == 200
    sheet = load_workbook(io.BytesIO(export.content)).active
    assert sheet["A1"].value == "Date" and sheet["AN1"].value == "B2C payment Status " and sheet.max_row == 3
    assert sheet["G2"].value == "NC2" and sheet["AB2"].value == 860   # oldest first; Trip Fare is a number
    assert sheet["V1"].value == "Total \nkms" and sheet["A1"].fill.fgColor.rgb == "00FFE600"
    assert sheet["E2"].value == 9000000001 and sheet["Q2"].number_format == "hh:mm"


@pytest.mark.parametrize("address, zone", [
    ("Flat 2, Sector 29, Gurugram", "Gurgaon"), ("Noida Sector 62", "Noida"), ("Rohini, New Delhi 110085", "Delhi"),
    ("Indirapuram, Gaziabad, Delhi NCR", "Ghaziabad"), ("Vaishali 201010", "Ghaziabad"),
    ("Sector 18, 201301", "Noida"), ("IGI Airport Terminal 3, New Delhi 110037", "Airport"),
    ("Gurgaon Noida link", ""), ("Somewhere unknown", ""), ("Alpha 1, Greater Noida", "Greater Noida"),
    ("Sector 19, Faridabad", "Faridabad"), ("NIT 121001", "Faridabad"), ("Terminal-2", "Airport"),
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


def _book(client, headers, day, **values):
    body = {"date": day, "company_name": "B2C", "pickup_time": "09:00", **values}
    return client.post(ENDPOINT, json=body, headers=headers).json()


def test_trips_come_one_page_at_a_time_latest_first(client, headers):
    for hour in range(5):
        _book(client, headers, "2026-11-10", pickup_time=f"0{hour}:00", guest_name=f"Guest {hour}")
    first = client.get(ENDPOINT, params={"page_size": 2}, headers=headers).json()
    assert (first["total"], first["page"], first["summary"]["trips"]) == (5, 1, 5)
    assert [t["pickup_time"] for t in first["trips"]] == ["04:00", "03:00"]
    last = client.get(ENDPOINT, params={"page_size": 2, "page": 3}, headers=headers).json()
    assert [t["pickup_time"] for t in last["trips"]] == ["00:00"]
    assert client.get(ENDPOINT, params={"page_size": 2, "page": 9}, headers=headers).json()["trips"] == []
    assert client.get(ENDPOINT, params={"page_size": 500}, headers=headers).status_code == 422


def test_tab_counts_cover_the_dates_and_each_tab_lists_only_its_trips(client, headers):
    done = {"drop_time": "10:00", "total_kms": "20", "trip_fare": "500", "driver_id": "EEED/VK/9001", "cab_no": "HR55ZZ0001"}
    _book(client, headers, "2026-11-10", guest_name="Closed Guest", **done)
    _book(client, headers, "2026-11-10", guest_name="Open Guest", driver_id="EEED/VK/9001", cab_no="HR55ZZ0001")
    _book(client, headers, "2026-11-10", guest_name="Stranger Guest", **{**done, "driver_id": "", "driver_name": "Nobody Known", "cab_no": "DL01AA0001"})
    _book(client, headers, "2026-11-12", guest_name="Other Day", **done)
    day = {"date_from": "2026-11-10", "date_to": "2026-11-10"}
    body = client.get(ENDPOINT, params={**day, "view": "fix"}, headers=headers).json()
    assert [t["guest_name"] for t in body["trips"]] == ["Stranger Guest"] and body["total"] == 1
    summary = body["summary"]
    assert (summary["trips"], summary["open"], summary["closed"], summary["need_fixing"]) == (3, 1, 2, 1)
    assert (summary["total_kms"], summary["trip_fare"], summary["total_trip_fare"]) == (40, 1000, 1000)
    opened = client.get(ENDPOINT, params={**day, "view": "open"}, headers=headers).json()
    assert [t["guest_name"] for t in opened["trips"]] == ["Open Guest"] and opened["total"] == 1
    assert client.get(ENDPOINT, params={**day, "view": "closed"}, headers=headers).json()["total"] == 2
    found = client.get(ENDPOINT, params={"q": "stranger"}, headers=headers).json()
    assert found["total"] == 1 and found["summary"]["need_fixing"] == 1
    assert client.get(ENDPOINT, params={"q": "100%"}, headers=headers).json()["total"] == 0


def test_summary_adds_up_the_chosen_tab_by_client_or_date(client, headers):
    done = {"drop_time": "10:00", "total_kms": "20", "trip_fare": "500", "toll": "50"}
    _book(client, headers, "2026-11-10", **done)
    _book(client, headers, "2026-11-10", company_name="EY")
    _book(client, headers, "2026-11-11", **done)
    groups = client.get(f"{ENDPOINT}/summary", params={"group": "company_name"}, headers=headers).json()["groups"]
    assert groups == [{"name": "B2C", "trips": 2, "open": 0, "total_kms": 40, "total_trip_fare": 1100},
                      {"name": "EY", "trips": 1, "open": 1, "total_kms": 0, "total_trip_fare": 0}]
    days = client.get(f"{ENDPOINT}/summary", params={"group": "date", "view": "closed"}, headers=headers).json()["groups"]
    assert [(g["name"], g["trips"]) for g in days] == [("2026-11-10", 1), ("2026-11-11", 1)]
    assert client.get(f"{ENDPOINT}/summary", params={"group": "remarks"}, headers=headers).status_code == 422


def test_dashboard_totals_follow_the_year_and_month_chosen(client, headers):
    done = {"drop_time": "10:00", "total_kms": "25", "trip_fare": "1000", "gst": "50"}
    _book(client, headers, "2026-01-05", **done)
    _book(client, headers, "2026-01-20")
    _book(client, headers, "2026-12-31", **done)
    _book(client, headers, "2025-01-09", **done)
    read = lambda **params: client.get(f"{ENDPOINT}/dashboard", params=params, headers=headers).json()   # noqa: E731
    everything = read()
    assert (everything["trips"], everything["closed"], everything["open"], everything["years"]) == (4, 3, 1, [2026, 2025])
    assert (everything["total_kms"], everything["trip_fare"], everything["total_trip_fare"]) == (75, 3000, 3150)
    assert (read(year=2026)["trips"], read(year=2026, month=1)["trips"], read(year=2026, month=12)["trips"]) == (3, 2, 1)
    assert read(month=1)["trips"] == 3 and read(year=2024)["trips"] == 0      # every January; a year with no trips
    assert client.get(f"{ENDPOINT}/dashboard", params={"month": 13}, headers=headers).status_code == 422


def test_dashboard_says_how_many_of_its_trips_are_pipeline_bookings(client, headers):
    _book(client, headers, "2026-11-10")
    client.post(ENDPOINT, json={"date": "2026-11-28", "company_name": "B2C"}, params={"pipeline": "true"}, headers=headers)
    totals = client.get(f"{ENDPOINT}/dashboard", params={"year": 2026, "month": 11}, headers=headers).json()
    assert (totals["trips"], totals["open"], totals["pipeline"]) == (2, 2, 1)


def _index(*drivers):
    index = MasterIndex(plates={"HR55ZZ0001"})
    for driver in drivers:
        row = DriverRow(**driver)
        index.by_truein[row.truein_id.replace("/", "")] = row
        index.by_phone.setdefault(row.mobile, []).append(row)
        index.by_name[row.name.lower().replace(" ", "")] = row
        index.all_names.append((row.name.lower().replace(" ", ""), row))
    return index


def test_driver_id_decides_the_match_and_name_only_stands_in_when_there_is_no_id():
    other = {"driver_id": "d2", "name": "OTHER DRIVER", "truein_id": "EEED/TP/9002", "mobile": "9000000002"}
    index = _index(DRIVER, other)
    trip = lambda **values: MisTrip(date="2026-11-02", company_name="EY", cab_no="HR55ZZ0001", **values)   # noqa: E731
    assert index.issues(trip(driver_id="eeed-vk 9001", driver_name="Somebody Else")) == []          # ID wins, punctuation ignored
    assert index.find_driver(trip(driver_id="EEED/TP/9002", driver_name="Test Driver")).name == "OTHER DRIVER"
    assert index.issues(trip(driver_id="EEED/VK/9002", driver_name="Other Driver")) == [
        "Driver ID EEED/VK/9002 not in driver master (OTHER DRIVER is EEED/TP/9002 there)"]
    assert index.issues(trip(driver_id="EEED/VK/7777", driver_name="Nobody")) == ["Driver ID EEED/VK/7777 not in driver master"]
    no_id = {"driver_id": "N/A", "driver_name": "Test Driver"}                                      # "N/A" is no ID
    assert index.issues(trip(**no_id, driver_contact="9000000001")) == []                           # name and mobile both his
    assert index.issues(trip(**no_id)) == ["Driver TEST DRIVER is in the master, but the trip has no mobile to confirm it"]
    assert index.issues(trip(driver_id="#N/A")) == ["Driver missing"]
    assert index.fill(trip(driver_name="Other", driver_contact="9000000002")).driver_id == "EEED/TP/9002"   # uploads get their ID
    assert index.fill(trip(driver_contact="9000000002")).driver_id == ""                            # a mobile with no name is not enough


def test_outside_cab_and_driver_are_not_checked_against_the_masters(client, headers):
    hired = _book(client, headers, "2026-11-10", hired="Yes", driver_id="Innova", driver_name="Test Driver",
                  driver_contact="9111111111", cab_no="DL01AA0001", guest_name="Hired Guest")
    assert hired["hired"] == "Yes" and hired["issues"] == []
    assert (hired["driver_id"], hired["driver_name"]) == ("Innova", "Test Driver")       # not replaced by the fleet driver
    own = _book(client, headers, "2026-11-10", driver_name="Nobody Known", cab_no="DL01AA0001", guest_name="Own Guest")
    assert own["hired"] == "" and len(own["issues"]) == 2                               # the same cab on an own trip is flagged
    body = client.get(ENDPOINT, params={"view": "fix"}, headers=headers).json()
    assert [t["guest_name"] for t in body["trips"]] == ["Own Guest"] and body["summary"]["need_fixing"] == 1
    empty = _book(client, headers, "2026-11-11", hired="yes", guest_name="Empty Hired")
    assert empty["issues"] == ["Driver missing", "Cab No missing"]
    again = client.post(f"{ENDPOINT}/copy", json={"ids": [hired["id"]], "date": "2026-11-12"}, headers=headers).json()
    copied = client.get(ENDPOINT, params={"date_from": "2026-11-12"}, headers=headers).json()["trips"][0]
    assert again["added"] == 1 and copied["hired"] == "Yes"
    blank_id = client.put(f"{ENDPOINT}/{hired['id']}", headers=headers,
                          json={"date": "2026-11-10", "company_name": "B2C", "hired": "Yes", "driver_name": "Hired Man", "cab_no": "DL01AA0001"})
    assert blank_id.json()["issues"] == []
    sheet = load_workbook(io.BytesIO(client.get(f"{ENDPOINT}/export", params={"q": "hired man"}, headers=headers).content)).active
    assert sheet["C2"].value == "Hired"


def test_excel_download_has_exactly_the_google_sheets_columns(client, headers):
    """The headings below are copied from row 1 of the team's Nov-2026 Google Sheet tab, line breaks and all."""
    sheet_headings = [
        "Date", "Dispatcher Name", "Driver ID", "Driver Name", "Driver Cont No", "Cab No", "DS no/Route No",
        "Company Name", "Guest Name", "Pickup Address", "Pickup Zone", "Pick up Time", "Drop Location", "Drop Zone",
        "Drop Time", "Trip End Date", "Trip Duration", "Hub Out", "Hub In", "Start Odometer", "End Odometer",
        "Total \nkms", "Dead  \nRun Kms", "Package", "Km/Extra/\nKm Charges", "Time Extra/\nTime Charges",
        "Night Charge \n11pm to 5am", "Trip Fare", "GST", "MCD", "Parking", "Toll", "Total Trip Fare", "Driver Hours",
        "Cash to \nbe Recieved", "Remarks", "MMT Base Cost", "MMT Peripaid Amt", "Duty closed by", "B2C payment Status ",
    ]
    _book(client, headers, "2026-11-10")
    sheet = load_workbook(io.BytesIO(client.get(f"{ENDPOINT}/export", headers=headers).content)).active
    assert [cell.value for cell in sheet[1]] == sheet_headings


def test_phone_shared_by_two_drivers_does_not_pick_the_wrong_one():
    shares = {"driver_id": "d2", "name": "SHARES PHONE", "truein_id": "EEED/VK/9002", "mobile": "9000000001"}
    index = _index(DRIVER, shares)
    trip = lambda **values: MisTrip(date="2026-11-02", company_name="MongoDB", cab_no="HR55ZZ0001", **values)   # noqa: E731
    assert index.fill(trip(driver_name="Shares Phone", driver_contact="9000000001")).driver_id == "EEED/VK/9002"
    assert index.fill(trip(driver_name="Test Driver", driver_contact="9000000001")).driver_id == "EEED/VK/9001"
    assert index.issues(trip(driver_name="Third Person", driver_contact="9000000001")) == [
        "Driver mobile is TEST DRIVER's in the master, but the name is Third Person"]
    assert index.issues(trip(driver_name="Nobody At All", driver_contact="9555555555")) == ["Driver not in driver master"]


def test_upload_check_lists_drivers_and_cabs_to_fix_and_adds_nothing(client, headers):
    rows = [_mmt_row("NC1"), _mmt_row("NC2", phone="+918000000000"), _mmt_row("NC3", phone="+918000000000"),
            dict(_mmt_row("NC4", plate="DL01AA0001"), **{"Driver Name": "New Person", "Driver Phone": "+917000000000"})]
    files = {"file": ("mmt.xlsx", _mmt_file(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    check = client.post(f"{ENDPOINT}/upload-mmt", files=files, data={"dispatcher_name": "Desk One", "dry_run": "true"}, headers=headers).json()
    assert (check["dry_run"], check["trips_in_file"], check["added"], check["need_fixing"]) == (True, 4, 4, 3)
    assert (check["drivers_to_fix"], check["driver_trips"], check["cabs_to_fix"], check["cab_trips"]) == (2, 3, 1, 1)
    assert check["fix_drivers"] == [
        {"name": "Test Driver", "mobile": "8000000000", "problem": "Driver TEST DRIVER is in the master with a different mobile", "trips": 2},
        {"name": "New Person", "mobile": "7000000000", "problem": "Driver not in driver master", "trips": 1}]
    assert check["fix_cabs"] == [{"cab_no": "DL01AA0001", "problem": "Cab DL01AA0001 not in vehicle master", "trips": 1}]
    assert client.get(ENDPOINT, headers=headers).json()["summary"]["trips"] == 0          # the check saved nothing
    assert _upload(client, headers, rows).json()["added"] == 4
    again = client.post(f"{ENDPOINT}/upload-mmt", files=files, data={"dispatcher_name": "Desk One", "dry_run": "true"}, headers=headers).json()
    assert (again["added"], again["skipped_existing"]) == (0, 4)       # a DS no already in the MIS is never added twice
    assert (again["drivers_to_fix"], again["cabs_to_fix"]) == (2, 1)   # the file's own problems are still listed
    twice = {"file": ("mmt.xlsx", _mmt_file([_mmt_row("NC9"), _mmt_row("NC9")]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    doubled = client.post(f"{ENDPOINT}/upload-mmt", files=twice, data={"dispatcher_name": "Desk One"}, headers=headers).json()
    assert (doubled["added"], doubled["repeated_in_file"], doubled["skipped_existing"]) == (1, 1, 0)
    assert client.get(f"{ENDPOINT}/lookups", headers=headers).json()["page_version"].startswith("20")
