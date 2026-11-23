"""MIS: MongoDB roster upload (guests grouped per Trip ID), its rate list, and the Pipeline flag."""
import csv
import io

from app.database import SessionLocal
from app.models import EeeTaxiRateCard
from app.services.auth import get_password_hash
from app.services.eee_taxi_rates import get_edit_password_hash, set_edit_password_hash
from app.services.mongodb_rates import MONGODB_RATE_ID
from tests.test_eee_taxi_mis import ENDPOINT, client, headers  # noqa: F401  (fixtures)

COLUMNS = [
    "Office", "Date", "Name", "Trip ID", "Registration", "Direction", "Shift Type/Time", "Employee Status",
    "Planned Pickup Time", "Actual Pickup Time", "Planned Drop Time", "Actual Drop Time",
    "Distance Travelled(KM)", "Selected Destination", "Billing Zone", "Nodal Point",
    "Planned Driver Name", "Planned Driver Contact No", "Actual Driver Name", "Actual Driver Contact No",
]


def _guest(trip_id, name, zone="Delhi", boarded=True, pickup="07:40", drop="08:53", planned="07:30",
           direction="LOGIN", kms="20", home="12 Test Lane, New Delhi"):
    day = "2026-11-03"
    office = "MONGODB_GURGAON"
    return {
        "Office": office, "Date": day, "Name": name, "Trip ID": trip_id, "Registration": "HR-55-ZZ-0001",
        "Direction": direction, "Shift Type/Time": "09:00",
        "Employee Status": "Boarded" if boarded else "Not Boarded",
        "Planned Pickup Time": f"{day} {planned}", "Actual Pickup Time": f"{day} {pickup}" if boarded else "--",
        "Planned Drop Time": f"{day} 08:45", "Actual Drop Time": f"{day} {drop}" if boarded else "--",
        "Distance Travelled(KM)": kms if boarded else "0",
        "Selected Destination": office if direction == "LOGIN" else home,
        "Billing Zone": zone, "Nodal Point": home if direction == "LOGIN" else "NA",
        "Planned Driver Name": "Test Driver", "Planned Driver Contact No": "9000000001 | 9000000009",
        "Actual Driver Name": "Test Driver" if boarded else "NA",
        "Actual Driver Contact No": "9000000001" if boarded else "",
    }


def _file(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _upload(client, headers, rows):
    files = {"file": ("mongodb.csv", _file(rows), "text/csv")}
    return client.post(f"{ENDPOINT}/upload-mongodb", files=files, data={"dispatcher_name": "Desk One"}, headers=headers)


def _trips(client, headers, **params):
    return client.get(ENDPOINT, headers=headers, params=params).json()["trips"]


def test_guests_of_one_trip_id_become_one_trip_with_first_pickup_and_last_drop(client, headers):
    rows = [_guest("T1", "Guest Two", pickup="07:55", planned="07:45", drop="08:53", kms="12"),
            _guest("T1", "Guest One", pickup="07:26", planned="07:20", drop="09:05", kms="31.5")]
    result = _upload(client, headers, rows)
    assert result.status_code == 200, result.text
    assert (result.json()["added"], result.json()["errors"]) == (1, [])
    trip, = _trips(client, headers)
    assert trip["company_name"] == "MongoDB" and trip["route_no"] == "T1" and trip["source"] == "mongodb"
    assert trip["guest_name"] == "Guest One/Guest Two"          # in boarding order
    assert (trip["pickup_time"], trip["drop_time"], trip["trip_duration"]) == ("07:26", "09:05", "01:39")
    assert (trip["pickup_zone"], trip["drop_location"], trip["drop_zone"]) == ("Delhi", "MONGODB GURGAON", "Gurgaon")
    assert (trip["package"], trip["trip_fare"], trip["total_trip_fare"], trip["total_kms"]) == ("1145", "1145", "1145", "31.5")
    assert all(trip[key] == "" for key in ("extra_km_charges", "extra_time_charges", "night_charge", "mcd", "parking", "gst", "toll"))
    assert trip["driver_id"] == "EEED/VK/9001" and trip["cab_no"] == "HR55ZZ0001"
    assert trip["status"] == "Closed" and trip["issues"] == []


def test_mixed_zones_take_the_highest_fare_and_count_guests_who_did_not_board(client, headers):
    rows = [_guest("T2", "Guest One", zone="Gurgaon"),
            _guest("T2", "Guest Two", zone="Noida/Gr. Noida/Ghaziabad", boarded=False),
            _guest("T3", "Guest Three", zone="gr gurgaon")]
    assert _upload(client, headers, rows).json()["added"] == 2
    fares = {t["route_no"]: t for t in _trips(client, headers)}
    assert fares["T2"]["trip_fare"] == "1445" and fares["T2"]["pickup_zone"] == "Noida/Gr. Noida/Ghaziabad"
    assert fares["T2"]["guest_name"] == "Guest One/Guest Two" and "Not boarded: Guest Two" in fares["T2"]["remarks"]
    assert fares["T3"]["trip_fare"] == "750"


def test_logout_trip_runs_from_the_office_to_the_last_drop(client, headers):
    rows = [_guest("T4", "Guest One", direction="LOGOUT", pickup="17:20", drop="18:05", home="1 Near Road, Gurgaon", zone="Gurgaon"),
            _guest("T4", "Guest Two", direction="LOGOUT", pickup="17:20", drop="18:40", home="9 Far Road, Faridabad", zone="NCR (Ex UP)")]
    _upload(client, headers, rows)
    trip, = _trips(client, headers)
    assert (trip["pickup_address"], trip["pickup_zone"]) == ("MONGODB GURGAON", "Gurgaon")
    assert (trip["drop_location"], trip["drop_zone"], trip["drop_time"]) == ("9 Far Road, Faridabad", "NCR (Ex UP)", "18:40")
    assert trip["trip_fare"] == "1050"


def test_trip_nobody_boarded_is_open_without_fare_and_closes_on_a_later_upload(client, headers):
    first = _upload(client, headers, [_guest("T5", "Guest One", boarded=False, planned="17:15")]).json()
    assert (first["added"], first["still_open"]) == (1, 1)
    trip, = _trips(client, headers)
    assert trip["status"] == "Open" and trip["pickup_time"] == "17:15"
    assert (trip["package"], trip["trip_fare"], trip["drop_time"]) == ("", "", "")
    client.put(f"{ENDPOINT}/{trip['id']}", json={**{k: trip[k] for k in ("date", "company_name", "route_no", "guest_name", "pickup_time")}, "toll": "80"}, headers=headers)

    again = _upload(client, headers, [_guest("T5", "Guest One", pickup="17:22", drop="18:10")]).json()
    assert (again["added"], again["updated"], again["skipped_existing"]) == (0, 1, 0)
    trip, = _trips(client, headers)
    assert trip["status"] == "Closed" and (trip["pickup_time"], trip["drop_time"]) == ("17:22", "18:10")
    assert (trip["trip_fare"], trip["toll"], trip["total_trip_fare"]) == ("1145", "80", "1225")   # typed toll is kept

    third = _upload(client, headers, [_guest("T5", "Guest One", pickup="17:30", drop="18:30")]).json()
    assert (third["added"], third["updated"], third["skipped_existing"]) == (0, 0, 1)   # closed trips are not touched


def test_zone_missing_from_the_rate_list_is_reported_and_left_open(client, headers):
    result = _upload(client, headers, [_guest("T6", "Guest One", zone="Jaipur")]).json()
    assert result["added"] == 1 and "Jaipur" in result["errors"][0]
    trip, = _trips(client, headers)
    assert trip["trip_fare"] == "" and trip["status"] == "Open"


def test_other_files_are_refused(client, headers):
    files = {"file": ("x.csv", b"Name,Amount\nA,1\n", "text/csv")}
    result = client.post(f"{ENDPOINT}/upload-mongodb", files=files, headers=headers)
    assert result.status_code == 400 and "MongoDB raw data report" in result.json()["detail"]


def test_rate_list_has_the_five_zones_and_edits_need_the_masters_password(client, headers):
    url = f"{ENDPOINT}/mongodb-rates"
    rates = client.get(url, headers=headers).json()["rates"]
    assert {z["zone"]: z["fare"] for z in rates["zones"]} == {
        "Delhi": "1145", "Gr. Gurgaon": "750", "Gurgaon": "725", "Noida/Gr. Noida/Ghaziabad": "1445", "NCR (Ex UP)": "1050"}
    with SessionLocal() as db:
        had_rate_card = db.get(EeeTaxiRateCard, 1) is not None
        password_before = get_edit_password_hash(db)
        set_edit_password_hash(db, get_password_hash("masters123"))
    rates["zones"][0]["fare"] = "1200"
    try:
        assert client.put(url, json={"edit_password": "wrong", "rates": rates}, headers=headers).status_code == 403
        assert client.put(url, json={"edit_password": "masters123", "rates": rates}, headers=headers).status_code == 200
        _upload(client, headers, [_guest("T7", "Guest One")])
        assert _trips(client, headers)[0]["trip_fare"] == "1200"
        repeated = {"zones": [{"zone": "Delhi", "fare": "1"}, {"zone": "delhi", "fare": "2"}]}
        assert client.put(url, json={"edit_password": "masters123", "rates": repeated}, headers=headers).status_code == 422
    finally:   # other test files expect the masters password and rate rows as they found them
        with SessionLocal() as db:
            db.query(EeeTaxiRateCard).filter(EeeTaxiRateCard.id == MONGODB_RATE_ID).delete()
            card = db.query(EeeTaxiRateCard).filter(EeeTaxiRateCard.id == 1)
            card.update({"edit_password_hash": password_before}) if had_rate_card else card.delete()
            db.commit()


def test_pipeline_shows_the_same_trip_and_removing_it_keeps_the_trip(client, headers):
    body = {"date": "2026-12-20", "company_name": "B2C", "guest_name": "Guest One", "pickup_time": "10:00"}
    booked = client.post(ENDPOINT, json=body, params={"pipeline": "true"}, headers=headers).json()
    client.post(ENDPOINT, json={**body, "guest_name": "Guest Two"}, headers=headers)
    assert booked["in_pipeline"] is True and booked["status"] == "Open"
    assert len(_trips(client, headers, date_from="2026-12-20", date_to="2026-12-20")) == 2   # both are in the MIS
    assert [t["guest_name"] for t in _trips(client, headers, pipeline="true")] == ["Guest One"]

    client.put(f"{ENDPOINT}/{booked['id']}", json={**body, "driver_name": "TEST DRIVER"}, headers=headers)
    assert _trips(client, headers, pipeline="true")[0]["driver_id"] == "EEED/VK/9001"   # one entry, two places

    removed = client.post(f"{ENDPOINT}/pipeline", json={"ids": [booked["id"]], "in_pipeline": False}, headers=headers)
    assert removed.json() == {"changed": 1}
    assert _trips(client, headers, pipeline="true") == []
    assert len(_trips(client, headers, date_from="2026-12-20", date_to="2026-12-20")) == 2


def test_closing_a_trip_takes_it_out_of_the_pipeline(client, headers):
    body = {"date": "2026-12-21", "company_name": "B2C", "guest_name": "Guest One", "pickup_time": "10:00"}
    booked = client.post(ENDPOINT, json=body, params={"pipeline": "true"}, headers=headers).json()
    closed = client.put(f"{ENDPOINT}/{booked['id']}", headers=headers,
                        json={**body, "drop_time": "11:00", "total_kms": "12", "trip_fare": "500"}).json()
    assert closed["status"] == "Closed" and closed["in_pipeline"] is False
    again = client.post(f"{ENDPOINT}/pipeline", json={"ids": [booked["id"]], "in_pipeline": True}, headers=headers)
    assert again.json() == {"changed": 0} and _trips(client, headers, pipeline="true") == []


def test_boarded_trip_without_a_billing_zone_is_reported(client, headers):
    result = _upload(client, headers, [_guest("T8", "Guest One", zone="--")]).json()
    assert result["added"] == 1 and "no Billing Zone" in result["errors"][0]
