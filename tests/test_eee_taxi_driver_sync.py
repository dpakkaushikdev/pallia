"""Driver master copied from Truein: mapping, safety when Truein fails, refresh timing and today's attendance."""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.database import SessionLocal, init_db
from app.models import EeeTaxiDriverMaster
from app.services import eee_taxi_driver_sync as sync
from app.services.eee_taxi_drivers import DriverRow, get_driver_rows
from app.services.eee_taxi_mis import MisTrip
from app.services.truein import TrueinError
from tests.test_eee_taxi_mis import ENDPOINT, _book, _index, client, headers  # noqa: F401  (fixtures)

STAFF = [
    {"empId": "EEED/VK/9001", "name": "Test  Driver", "mobile": "+91 90000 00001", "status": "active",
     "father_name": "FATHER ONE", "email": "one@example.com", "department": "ETS", "joining_date": "2024-02-05"},
    {"empId": "EEED/TP/9002", "name": "LEFT DRIVER", "mobile": "9000000002", "status": "inactive", "joining_date": ""},
    {"empId": "EEED/VK/9003", "name": "RETYPED ID", "mobile": "9000000003", "status": "active"},
    {"empId": "", "name": "No Id"}, {"empId": "EEED/VK/9001", "name": "Listed Twice"},
]
OLD = [
    DriverRow(driver_id="d1", name="TEST DRIVER", truein_id="EEED/VK/9001", mobile="9111111111", dl_expiry="01/02/2030",
              alternate_mobile="9222222222", branch="Vasant Kunj", emergency_contact_name="KIN ONE", status="Deactive"),
    DriverRow(driver_id="d3", name="Retyped Id", truein_id="EEE/VK-0903", mobile="9000000003", dl_expiry="05/06/2031"),
    DriverRow(driver_id="d9", name="NOT IN TRUEIN", mobile="9000000009"),
]


@pytest.fixture()
def truein(monkeypatch):
    """A Truein that answers from a list the test controls, and counts how often it is asked."""
    calls = {"staff": 0, "punches": 0, "staff_reply": list(STAFF), "punch_reply": []}

    def staff():
        calls["staff"] += 1
        if isinstance(calls["staff_reply"], Exception):
            raise calls["staff_reply"]
        return calls["staff_reply"]

    def punches(_day):
        calls["punches"] += 1
        if isinstance(calls["punch_reply"], Exception):
            raise calls["punch_reply"]
        return calls["punch_reply"]

    init_db()   # the tables exist before any test in this file runs alone
    monkeypatch.setattr(settings, "truein_subscription_key", "test-key")
    monkeypatch.setattr(sync, "fetch_staff", staff)
    monkeypatch.setattr(sync, "fetch_punches", punches)
    with SessionLocal() as db:
        db.query(EeeTaxiDriverMaster).delete()
        db.add(EeeTaxiDriverMaster(id=1, rows=[row.model_dump() for row in OLD]))
        db.commit()
    yield calls
    with SessionLocal() as db:
        db.query(EeeTaxiDriverMaster).delete()
        db.commit()


def test_truein_staff_become_master_rows_and_keep_the_web_apps_own_details():
    rows = {row.truein_id: row for row in sync.rows_from_truein(STAFF, OLD)}
    assert list(rows) == ["EEED/VK/9001", "EEED/TP/9002", "EEED/VK/9003"]      # no ID and the repeat are left out
    one = rows["EEED/VK/9001"]
    assert (one.driver_id, one.name, one.mobile, one.status) == ("d1", "Test Driver", "9000000001", "Active")
    assert (one.father_name, one.email, one.department, one.date_of_joining) == ("FATHER ONE", "one@example.com", "ETS", "05/02/2024")
    assert (one.dl_expiry, one.alternate_mobile, one.branch, one.emergency_contact_name) == ("01/02/2030", "9222222222", "Vasant Kunj", "KIN ONE")
    assert rows["EEED/TP/9002"].status == "Deactive" and rows["EEED/TP/9002"].dl_expiry == ""
    retyped = rows["EEED/VK/9003"]                                              # same mobile and name, mistyped ID before
    assert (retyped.driver_id, retyped.dl_expiry) == ("d3", "05/06/2031")
    respelled = DriverRow(driver_id="d4", name="Mohd Respelled", truein_id="EEED/NIPS/0904", mobile="9000000004", dl_expiry="07/08/2032")
    person = {"empId": "EERD/NIPS/VK/0904", "name": "MOHAMMAD RESPELT", "mobile": "9000000004", "status": "active"}
    assert sync.rows_from_truein([person], [respelled])[0].dl_expiry == "07/08/2032"       # same mobile and ID number
    assert sync.rows_from_truein([{**person, "empId": "EEED/VK/0777"}], [respelled])[0].dl_expiry == ""


def test_sync_replaces_the_master_and_a_failed_or_empty_reply_leaves_it_alone(truein):
    with SessionLocal() as db:
        assert sync.sync_drivers(db) == {"ok": True, "drivers": 3, "added": 1, "removed": 1, "changed": 2}
        assert [row.name for row in get_driver_rows(db)] == ["Test Driver", "LEFT DRIVER", "RETYPED ID"]
        assert sync.sync_drivers(db) == {"ok": True, "drivers": 3, "added": 0, "removed": 0, "changed": 0}
        for bad, said in ((TrueinError("Truein said: Too many requests"), "Too many requests"), ([], "no staff"),
                          ([STAFF[0]], "only 1 staff against 3")):
            truein["staff_reply"] = bad
            result = sync.sync_drivers(db)
            assert result["ok"] is False and said in result["error"]
            assert len(get_driver_rows(db)) == 3 and said in db.get(EeeTaxiDriverMaster, 1).sync_error


def test_the_master_is_refreshed_only_when_the_last_attempt_is_half_an_hour_old(truein):
    with SessionLocal() as db:
        sync.sync_if_stale(db)
        sync.sync_if_stale(db)
        assert truein["staff"] == 1
        db.get(EeeTaxiDriverMaster, 1).synced_at = datetime.utcnow() - timedelta(minutes=31)
        db.commit()
        sync.sync_if_stale(db)
        assert truein["staff"] == 2
        settings.truein_subscription_key = ""          # no key: Truein is never asked
        db.get(EeeTaxiDriverMaster, 1).synced_at = None
        db.commit()
        sync.sync_if_stale(db)
        assert truein["staff"] == 2


def test_todays_clock_ins_are_read_at_most_every_ten_minutes_and_survive_a_refusal(truein):
    today = (datetime.utcnow() + sync.IST_OFFSET).strftime("%Y-%m-%d")
    truein["punch_reply"] = [{"empid": "EEED/VK/9001", "inTime": f"{today} 07:00:00", "outTime": f"{today} 08:00:00"},
                             {"empid": "eeed-vk 9001", "inTime": f"{today} 09:12:33", "outTime": "-"}]
    with SessionLocal() as db:
        assert sync.attendance_today(db) == {"EEEDVK9001": {"in": "09:12", "out": ""}}     # the later punch
        truein["punch_reply"] = TrueinError("Truein said: Too many requests")
        assert sync.attendance_today(db)["EEEDVK9001"]["in"] == "09:12" and truein["punches"] == 1
        db.get(EeeTaxiDriverMaster, 1).attendance_at = datetime.utcnow() - timedelta(minutes=11)
        db.commit()
        assert sync.attendance_today(db)["EEEDVK9001"]["in"] == "09:12" and truein["punches"] == 2   # refused: last copy
        stored = db.get(EeeTaxiDriverMaster, 1)
        stored.attendance = {"day": "2020-01-01", "staff": {"EEEDVK9001": {"in": "09:00", "out": ""}}}
        db.commit()
        assert sync.attendance_today(db) == {}                                              # yesterday's is not today's


def test_driver_api_shows_truein_state_and_the_sync_button_reports_what_changed(truein, client, headers):
    today = (datetime.utcnow() + sync.IST_OFFSET).strftime("%Y-%m-%d")
    truein["punch_reply"] = [{"empid": "EEED/VK/9001", "inTime": f"{today} 09:12:00", "outTime": "-"}]
    with SessionLocal() as db:       # the client fixture reset the masters; start from the old list again
        db.query(EeeTaxiDriverMaster).delete()
        db.add(EeeTaxiDriverMaster(id=1, rows=[row.model_dump() for row in OLD]))
        db.commit()
    body = client.get("/api/eee-taxi/drivers", headers=headers).json()
    assert body["from_truein"] is True and body["synced_at"] and body["sync_error"] is None
    assert [row["name"] for row in body["rows"]] == ["Test Driver", "LEFT DRIVER", "RETYPED ID"]
    assert body["attendance"] == {"EEED/VK/9001": {"in": "09:12", "out": ""}}
    lookups = client.get(f"{ENDPOINT}/lookups", headers=headers).json()
    assert [d["today"] for d in lookups["drivers"]] == [{"in": "09:12", "out": ""}, None, None]
    assert client.post("/api/eee-taxi/drivers/sync", headers=headers).json()["sync"]["changed"] == 0
    truein["staff_reply"] = TrueinError("Truein said: Too many requests")
    refused = client.post("/api/eee-taxi/drivers/sync", headers=headers)
    assert refused.status_code == 502 and "not changed" in refused.json()["detail"]


def test_a_driver_who_has_left_is_flagged_only_on_a_trip_still_to_run(client, headers):
    left = {"driver_id": "d2", "name": "LEFT DRIVER", "truein_id": "EEED/TP/9002", "mobile": "9000000002", "status": "Deactive"}
    index = _index(left)
    trip = lambda **values: MisTrip(date="2026-11-02", company_name="EY", cab_no="HR55ZZ0001", driver_id="EEED/TP/9002", **values)   # noqa: E731
    assert index.issues(trip()) == ["Driver is Deactive in the master"]
    assert index.issues(trip(drop_time="10:00", total_kms="12", trip_fare="500")) == []
    with SessionLocal() as db:
        stored = db.get(EeeTaxiDriverMaster, 1)
        stored.rows = [*stored.rows, left]
        db.commit()
    crew = {"driver_id": "EEED/TP/9002", "cab_no": "HR55ZZ0001"}
    _book(client, headers, "2026-11-10", guest_name="Ran Before Leaving", drop_time="10:00", total_kms="12", trip_fare="500", **crew)
    _book(client, headers, "2026-11-10", guest_name="Booked After Leaving", **crew)
    body = client.get(ENDPOINT, params={"view": "fix"}, headers=headers).json()
    assert [t["guest_name"] for t in body["trips"]] == ["Booked After Leaving"] and body["summary"]["need_fixing"] == 1
