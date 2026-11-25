"""MIS: reading the old month sheets, the Others client and adding a client to the list."""
from app.database import SessionLocal
from app.models import EeeTaxiMisClient
from app.services.eee_taxi_mis_sheet import full_plate, header_keys, is_trip_row, sheet_row_to_trip, split_driver_cab
from app.services.eee_taxi_mis_zones import set_added_companies
from tests.test_eee_taxi_mis import ENDPOINT, client, headers  # noqa: F401  (fixtures)

ROW = {"date": "03/02/2026", "company_name": "Capgemini", "guest_name": "Guest One", "pickup_time": "4:21 AM",
       "drop_time": "6:14 AM", "trip_end_date": "03/02/2026", "total_kms": "35", "trip_fare": "1,250"}


def test_old_tab_headers_map_to_mis_columns():
    assert header_keys(["Date", "Car No", "Company name", "Pickup Location", "Total \nkms", "Toll/MCD", "Notes"]) == [
        "date", "cab_no", "company_name", "pickup_address", "total_kms", "mcd", None]
    assert not is_trip_row({"trip_duration": "0:00", "total_kms": "0"}) and is_trip_row({"guest_name": "Guest One"})


def test_sheet_row_reads_am_pm_times_and_day_first_dates():
    trip = sheet_row_to_trip(ROW, (2026, 2))
    assert (trip.date, trip.pickup_time, trip.drop_time, trip.trip_duration) == ("2026-02-03", "04:21", "06:14", "01:53")
    assert (trip.trip_fare, trip.total_kms, trip.status, trip.remarks) == ("1250", "35", "Closed", "")


def test_month_first_dates_are_read_by_the_tabs_month():
    trip = sheet_row_to_trip({**ROW, "date": "02/03/2026", "trip_end_date": "02/13/2026"}, (2026, 2))
    assert trip.date == "2026-02-03" and trip.trip_end_date == ""      # ten days later is not this trip's end
    assert "Trip End Date in sheet: 02/13/2026" in trip.remarks
    assert sheet_row_to_trip({**ROW, "date": "02/13/2026", "trip_end_date": "02/14/2026"}, (2026, 2)).trip_end_date == "2026-02-14"


def test_cell_the_mis_cannot_hold_is_blanked_and_kept_in_remarks():
    trip = sheet_row_to_trip({**ROW, "cash_to_receive": "Payment Done", "total_kms": "-58", "drop_time": "1840",
                              "start_odometer": "500", "end_odometer": "400", "remarks": "Guard route"}, (2026, 2))
    assert (trip.cash_to_receive, trip.total_kms, trip.drop_time, trip.start_odometer) == ("", "", "18:40", "")
    assert trip.remarks.startswith("Guard route | ") and "Cash to be Recieved in sheet: Payment Done" in trip.remarks
    assert "Total kms in sheet: -58" in trip.remarks and "Start Odometer in sheet: 500" in trip.remarks


def test_typed_total_kms_is_kept_when_odometers_show_less():
    trip = sheet_row_to_trip({**ROW, "total_kms": "91", "start_odometer": "52987", "end_odometer": "53009"}, (2026, 2))
    assert trip.total_kms == "91"
    assert sheet_row_to_trip({**ROW, "total_kms": "", "start_odometer": "52987", "end_odometer": "53009"}).total_kms == "22"


def test_unknown_client_goes_under_others_with_its_name_and_typos_are_folded():
    trip = sheet_row_to_trip({**ROW, "company_name": "Marrige Event"}, (2026, 2))
    assert (trip.company_name, trip.company_other) == ("Others", "Marrige Event")
    assert sheet_row_to_trip({**ROW, "company_name": "Superme Gartner"}).company_name == "Supreme Gartner"
    assert sheet_row_to_trip({**ROW, "company_name": "Coforge"}).company_other == ""


def test_row_without_a_readable_date_is_refused():
    try:
        sheet_row_to_trip({**ROW, "date": "Vasant Kunj"}, (2026, 2))
    except ValueError as exc:
        assert "Date" in str(exc)
    else:
        raise AssertionError("a row with no date was accepted")


def test_others_keeps_the_typed_name_and_other_clients_drop_it(client, headers):
    body = {"date": "2026-11-20", "company_name": "Others", "company_other": "Wedding Party", "pickup_time": "10:00"}
    trip = client.post(ENDPOINT, json=body, headers=headers).json()
    assert (trip["company_name"], trip["company_other"]) == ("Others", "Wedding Party")
    assert client.get(ENDPOINT, headers=headers, params={"q": "wedding"}).json()["summary"]["trips"] == 1
    changed = client.put(f"{ENDPOINT}/{trip['id']}", json={**body, "company_name": "B2C"}, headers=headers).json()
    assert (changed["company_name"], changed["company_other"]) == ("B2C", "")


def test_client_added_on_the_page_can_be_used_and_is_not_added_twice(client, headers):
    try:
        added = client.post(f"{ENDPOINT}/clients", json={"name": "  Test  Client Ltd "}, headers=headers)
        assert added.status_code == 201 and added.json()["name"] == "Test Client Ltd"
        companies = client.get(f"{ENDPOINT}/lookups", headers=headers).json()["companies"]
        assert companies[-2:] == ["Test Client Ltd", "Others"] and {"Google", "Coforge", "Wipro"} <= set(companies)
        trip = client.post(ENDPOINT, json={"date": "2026-11-20", "company_name": "test client ltd"}, headers=headers)
        assert trip.status_code == 201 and trip.json()["company_name"] == "Test Client Ltd"
        for repeat in ("TEST CLIENT LTD", "mongo db", "Others"):
            assert client.post(f"{ENDPOINT}/clients", json={"name": repeat}, headers=headers).status_code == 409
    finally:
        with SessionLocal() as db:
            db.query(EeeTaxiMisClient).delete()
            db.commit()
        set_added_companies([])


def test_pipeline_tab_short_cab_numbers_become_full_plates():
    plates = {"HR55ZZ0001", "HR55ZZ5215", "DL1ZZ5215"}
    assert full_plate("0001", plates) == "HR55ZZ0001"
    assert full_plate("5215", plates) == "5215" and full_plate("9999", plates) == "9999"   # two cabs, or none
    assert full_plate("HR55ZZ0001", plates) == "HR55ZZ0001"
    assert split_driver_cab("Test Driver 0001", "") == ("Test Driver", "0001")
    assert split_driver_cab("Test Driver 0001", "5215") == ("Test Driver 0001", "5215")
    assert split_driver_cab("Test Driver", "") == ("Test Driver", "")


def test_pipeline_lists_trips_from_a_given_day_onward(client, headers):
    for day in ("2026-12-03", "2026-12-01", "2026-12-02"):
        client.post(ENDPOINT, json={"date": day, "company_name": "B2C", "pickup_time": "09:00"},
                    params={"pipeline": "true"}, headers=headers)
    listed = client.get(ENDPOINT, headers=headers, params={"pipeline": "true", "date_from": "2026-12-02"}).json()["trips"]
    assert sorted(t["date"] for t in listed) == ["2026-12-02", "2026-12-03"]
