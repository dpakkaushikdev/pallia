"""EY fare boundaries, CSV validation, PDF/XML agreement and PWC isolation."""
from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pypdf import PdfReader
from sqlalchemy import create_engine, inspect, text

from app import database
from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiBatch, EeeTaxiInvoice, EeeTaxiInvoiceStatus, EeeTaxiCostCentre, EeeTaxiRateCard, User, UserRole
from app.services.auth import get_password_hash
from app.services.eee_taxi_clients import CLIENT_MASTER
from app.services.eee_taxi_csv import REQUIRED_HEADERS, parse_eee_taxi_csv
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD, get_rate_card, set_edit_password_hash
from app.services.eee_taxi_pipeline import batch_rates, batch_rows, build_invoice_pdf
from app.services.eee_taxi_profiles import detect_client_profile
from app.services.eee_taxi_tally import voucher_for_row
from app.services.ey_clients import ey_tax
from app.services.ey_csv import parse_ey_csv
from app.services.ey_fares import calculate_ey_fare, fare_description, apply_ey_fares, generate_ey_calc_csv, parse_ey_overrides
from app.services.ey_rates import EyRateCard, get_ey_rates, save_ey_rates
from app.services.ey_tally import ey_tally_ledgers
from app.services.tally_export import render_tally_xml
from tests.test_eee_taxi_csv import HEADER, TRIP_1


def ey_csv(**changes):
    values = {name: "" for name in REQUIRED_HEADERS.values()}
    values.update({"Date": "23/04/2026", "Cab No": "HR55AW2048", "DS no/Route No": "220426-NCR-0418",
                   "Guest Name": "Armaan Goel", "Pickup Address": "Sector 82A, Gurugram",
                   "Drop Location": "Terminal 2", "Pick up Time": "07:00", "Drop Time": "08:00",
                   "Trip Duration": "1:00", "Total kms": "10", "Package": "P2P", "Trip Fare": "999",
                   "MCD": "10", "Parking": "10", "Toll": "10", "Entity": "Ernst & Young LLP",
                   "Entity Gst": "06AAEFE1763C1ZW", "Company Name": "EY", "Eng Code": "E-45303477"})
    values.update(changes)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=values.keys())
    writer.writeheader()
    writer.writerow(values)
    return buf.getvalue().encode("utf-8-sig")


def ey_xlsx(**changes):
    workbook = Workbook()
    sheet = workbook.active
    for values in csv.reader(io.StringIO(ey_csv(**changes).decode("utf-8-sig"))):
        sheet.append(values)
    headers = {cell.value: cell.column for cell in sheet[1]}
    sheet.cell(2, headers["Date"], date(2026, 4, 23))
    sheet.cell(2, headers["Pick up Time"], time(7, 0))
    sheet.cell(2, headers["Drop Time"], time(8, 0))
    sheet.cell(2, headers["Trip Duration"], timedelta(hours=1))
    sheet.cell(2, headers["Trip Duration"]).number_format = "[h]:mm"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def row(**changes):
    return parse_ey_csv(ey_csv(**changes), EyRateCard())[1][0]


@pytest.mark.parametrize("duration,kms,expected", [
    ("1:00", "10", "322.00"), ("0:00", "0", "322.00"),
    ("0:10", "20", "420.00"), ("1:00", "20", "487.50"),
    ("1:45", "42", "1017.00"), ("1:00", "20.5", "498.00"),
])
def test_p2p_formula_minimum_and_grace(duration, kms, expected):
    result = calculate_ey_fare(row(**{"Trip Duration": duration, "Total kms": kms}), EyRateCard())
    assert result.fare == Decimal(expected)
    assert result.night == 0


@pytest.mark.parametrize("pickup,duration,night,fare", [("22:00", "1:00", 0, "322"), ("23:00", "2:00", 250, "617.50"), ("04:30", "1:00", 250, "572")])
def test_p2p_night_charge_applies_when_trip_overlaps_night_window(pickup, duration, night, fare):
    result = calculate_ey_fare(row(**{"Pick up Time": pickup, "Trip Duration": duration}), EyRateCard())
    assert result.night == night
    assert result.fare == Decimal(fare)


def test_ey_p2p_invoice_description_matches_reference_format():
    rates = EyRateCard()
    trip = apply_ey_fares([row(**{"Pick up Time": "17:00", "Trip Duration": "0:46", "Total kms": "5"})], rates)[0]
    lines = fare_description(trip, rates)
    assert "Total Hrs 46 Mins (31*1.5 = 46.5)" in lines
    assert "Total Kms 5 Km (5*21.00 = 105)" in lines
    assert "Night Charge = 0" in lines
    assert "Total Fare without Tax = 322" in lines
    assert not any("Minimum fare" in line or "grace" in line.lower() for line in lines)
    assert not any("23:00" in line or "05:00" in line for line in lines)


def test_ey_rental_extra_time_description_shows_duration_grace_and_calculation():
    rates = EyRateCard()
    trip = apply_ey_fares([row(**{"Package": "900", "Trip Duration": "4:49"})], rates)[0]
    lines = fare_description(trip, rates)
    assert "Extra Hrs 0:49 (49-15=34*2)=68" in lines
    assert not any("Extra minutes after" in line for line in lines)


def test_ey_rental_zero_extra_time_shows_direct_grace_calculation():
    rates = EyRateCard()
    trip = apply_ey_fares([row(**{"Package": "900", "Trip Duration": "4:00"})], rates)[0]
    assert "Extra Hrs 0:00 (0-15=0*2)=0" in fare_description(trip, rates)


def test_ey_rental_kilometres_and_vehicle_model_appear_in_invoice(tmp_path):
    rates = EyRateCard()
    trip = apply_ey_fares([row(**{"Package": "1800", "Total kms": "88"})], rates)[0]
    lines = fare_description(trip, rates)
    assert "Extra Kms 8 Km (8*14.00 = 112)" in lines

    pdf_path, _ = build_invoice_pdf(
        trip, "EY/26-27/0001", date(2026, 10, 7), rates, tmp_path,
        cost_centres={"HR55AW2048": "HR55AW2048(TIGOR-EV)"},
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    assert "Motor Vehicle No." in text
    assert "HR55AW2048(TIGOR-EV)" in text
    assert "Extra Kms 8 Km (8*14.00 = 112)" in text


@pytest.mark.parametrize("pkg,duration,kms,label,fare", [
    (900, "4:00", "40", "4/40", "900"), (900, "4:15", "40", "4/40", "900"),
    (900, "4:16", "40", "4/40", "902"), (900, "5:00", "40", "4/40", "990"),
    (900, "5:01", "40", "6/60", "1300"), (900, "6:30", "65", "6/60", "1400"),
    (1300, "6:15", "60", "6/60", "1300"), (1300, "6:16", "60", "6/60", "1302"),
    (1300, "7:00", "70", "6/60", "1530"), (1300, "7:01", "70", "8/80", "1800"),
    (1300, "6:00", "70.1", "8/80", "1800"), (900, "7:01", "40", "8/80", "1800"),
    (1800, "1:00", "10", "8/80", "1800"), (1800, "8:16", "81", "8/80", "1816"),
])
def test_rental_packages_extras_and_upgrade_boundaries(pkg, duration, kms, label, fare):
    result = calculate_ey_fare(row(**{"Package": str(pkg), "Trip Duration": duration, "Total kms": kms}), EyRateCard())
    assert result.package_label == label
    assert result.fare == Decimal(fare)


@pytest.mark.parametrize("start,duration,night", [("22:00", "1:00", 0), ("23:00", "1:00", 250),
    ("04:59", "0:01", 250), ("05:00", "1:00", 0), ("22:00", "8:00", 250)])
def test_rental_night_window_including_crossing_midnight(start, duration, night):
    result = calculate_ey_fare(row(**{"Package": "1800", "Pick up Time": start, "Trip Duration": duration}), EyRateCard())
    assert result.night == night


def test_ey_night_charge_boundary_supports_minutes():
    rates = EyRateCard(night_start_hour=23, night_start_minute=1)
    before_start = calculate_ey_fare(row(**{"Pick up Time": "23:00", "Trip Duration": "0:01"}), rates)
    at_start = calculate_ey_fare(row(**{"Pick up Time": "23:01", "Trip Duration": "0:01"}), rates)
    assert before_start.night == 0
    assert at_start.night == 250


@pytest.mark.parametrize("changes", [{"Eng Code": ""}, {"Total kms": "NaN"}, {"Total kms": "broken"},
    {"Trip Duration": "1:99"}, {"Pick up Time": "29:00"}, {"Parking": "-10"}, {"Package": "900.5"},
    {"Entity Gst": "06AAEFP1428R1ZW"}])
def test_invalid_ey_input_is_rejected(changes):
    with pytest.raises(ValueError):
        row(**changes)


def test_client_profiles_cannot_be_mixed():
    with pytest.raises(ValueError, match="selected client master"):
        parse_eee_taxi_csv(ey_csv(), DEFAULT_RATE_CARD)
    with pytest.raises(ValueError, match="Eng Code"):
        parse_ey_csv(ey_csv().replace(b"Eng Code", b"Another Code"), EyRateCard())


def test_company_name_detects_client_and_rejects_ambiguous_files():
    assert detect_client_profile(ey_csv()) == "ey"
    pwc = (HEADER + TRIP_1).encode()
    assert detect_client_profile(pwc) == "pwc"
    ey_text = ey_csv().decode("utf-8-sig")
    header, row_text = ey_text.splitlines()[:2]
    with pytest.raises(ValueError, match="both PWC and EY"):
        detect_client_profile((header + "\n" + row_text + "\n" + row_text.replace(",EY,", ",PWC," )).encode())
    with pytest.raises(ValueError, match='"Company Name"'):
        detect_client_profile(ey_csv().replace(b"Company Name,", b"Customer,"))


def test_excel_trip_upload_is_converted_before_ey_calculation_and_batch(ey_client):
    files = {"csv_file": ("EY Trips.xlsx", ey_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    calculated = ey_client.post("/api/eee-taxi/calculate", files=files)
    assert calculated.status_code == 200, calculated.text
    assert calculated.headers["X-Client-Profile"] == "ey"
    assert "Calc_Trip_Fare" in calculated.content.decode("utf-8-sig")

    preview = ey_client.post("/api/eee-taxi/preview", files=files)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"][0]["entity_name"] == "Ernst & Young LLP"
    assert preview.json()["rows"][0]["eng_code"] == "E-45303477"

    started = ey_client.post(
        "/api/eee-taxi/batch", files=files,
        data={"invoice_date": "2026-05-08", "start_suffix": 157, "sign_mode": "dummy"},
    )
    assert started.status_code == 200, started.text
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, started.json()["batch_id"])
        assert batch.csv_filename == "EY Trips.xlsx"
        assert detect_client_profile(batch.csv_data) == "ey"


def test_calculated_csv_roundtrip_keeps_toll_separate():
    rates = EyRateCard()
    headers, rows = parse_ey_csv(ey_csv(), rates)
    calculated = generate_ey_calc_csv(rows, headers, rates)
    values = list(csv.DictReader(io.StringIO(calculated.decode("utf-8-sig"))))[0]
    assert Decimal(values["Calc_Trip_Fare"]) == 322
    assert Decimal(values["Calc Total"]) == Decimal("369.60")
    adjusted = apply_ey_fares(rows, rates, parse_ey_overrides(calculated, rows))[0]
    assert adjusted.trip_fare == 322 and adjusted.parking == 30
    assert adjusted.total_amount == 0
    with pytest.raises(ValueError, match="minimum"):
        apply_ey_fares(rows, rates, {0: Decimal("277.50")})


def test_p2p_override_cannot_remove_night_charge():
    rates = EyRateCard()
    rows = [row(**{"Pick up Time": "23:00", "Trip Duration": "2:00"})]
    with pytest.raises(ValueError, match="minimum plus night charge"):
        apply_ey_fares(rows, rates, {0: Decimal("571.99")})


def test_ey_local_and_interstate_tax():
    assert ey_tax(Decimal("322"), True) == (Decimal("8.05"), Decimal("8.05"), Decimal("0"))
    assert ey_tax(Decimal("322"), False) == (Decimal("0"), Decimal("0"), Decimal("16.10"))


def test_upgrade_migration_defaults_old_batches_to_pwc(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE eee_taxi_batches (id VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO eee_taxi_batches (id) VALUES ('old')"))
    monkeypatch.setattr(database, "engine", engine)
    database._migrate_existing_db()
    database._migrate_existing_db()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT client_profile FROM eee_taxi_batches WHERE id='old'")).scalar() == "pwc"
    assert "client_profile" in {c["name"] for c in inspect(engine).get_columns("eee_taxi_batches")}


@pytest.fixture
def ey_client():
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.query(EeeTaxiInvoice).delete()
            db.query(EeeTaxiBatch).delete()
            db.query(EeeTaxiRateCard).filter(EeeTaxiRateCard.id == 2).delete()
            user = db.query(User).filter(User.email == "ey-admin@test.com").first()
            if user is None:
                db.add(User(email="ey-admin@test.com", hashed_password=get_password_hash("Password123"), role=UserRole.ADMIN, is_active=True))
            if not db.query(EeeTaxiCostCentre).filter_by(vehicle_no="HR55AW2048").first():
                db.add(EeeTaxiCostCentre(vehicle_no="HR55AW2048", cost_centre="HR55AW2048"))
            db.commit()
        login = client.post("/api/auth/login", json={"email": "ey-admin@test.com", "password": "Password123"})
        client.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})
        yield client
        with SessionLocal() as db:
            db.query(EeeTaxiInvoice).delete()
            db.query(EeeTaxiBatch).delete()
            db.query(EeeTaxiRateCard).filter(EeeTaxiRateCard.id == 2).delete()
            db.commit()


def test_ey_master_password_and_pwc_isolation(ey_client):
    with SessionLocal() as db:
        before = get_rate_card(db)
        set_edit_password_hash(db, get_password_hash("masters123"))
    rates = ey_client.get("/api/eee-taxi/ey/rates").json()["rates"]
    rates["p2p_per_km"] = "25"
    url = "/api/eee-taxi/ey/rates"
    assert ey_client.put(url, json={"edit_password": "wrong", "rates": rates}).status_code == 403
    assert ey_client.put(url, json={"edit_password": "masters123", "rates": rates}).status_code == 200
    with SessionLocal() as db:
        assert get_rate_card(db) == before
        assert get_ey_rates(db).p2p_per_km == 25
    anonymous = TestClient(app)
    assert anonymous.get(url).status_code == 401


def test_client_masters_include_workbook_ey_registrations_and_save_separately(ey_client):
    with SessionLocal() as db:
        set_edit_password_hash(db, get_password_hash("masters123"))
    ey_url = "/api/eee-taxi/clients?client_profile=ey"
    pwc_url = "/api/eee-taxi/clients?client_profile=pwc"
    ey = ey_client.get(ey_url).json()
    assert len(ey["rows"]) == 84
    expected = {"19AAEFE1763C1ZP", "29AAEFE1763C2ZN", "07AAEFE1763C1ZU", "27AAEFE1763C1ZS", "06AACCP8967E1Z5"}
    assert expected <= {row["gstin"] for row in ey["rows"]}
    assert all(row["entity_name"] and row["address"] and row["state_name"] for row in ey["rows"])

    updated = [dict(gstin=r["gstin"], entity_name=r["entity_name"], address=r["address"]) for r in ey["rows"]]
    response = ey_client.put(ey_url, json={"edit_password": "masters123", "rows": updated})
    assert response.status_code == 200, response.text
    assert len(ey_client.get(ey_url).json()["rows"]) == 84
    assert len(ey_client.get(pwc_url).json()["rows"]) == len(CLIENT_MASTER)


def test_admin_can_reset_forgotten_masters_edit_password(ey_client):
    with SessionLocal() as db:
        set_edit_password_hash(db, get_password_hash("old-edit-password"))
    reset = ey_client.post("/api/eee-taxi/rates/password/reset", json={"new_password": "new-edit-password"})
    assert reset.status_code == 200, reset.text
    assert ey_client.post("/api/eee-taxi/rates/unlock", json={"password": "old-edit-password"}).status_code == 403
    assert ey_client.post("/api/eee-taxi/rates/unlock", json={"password": "new-edit-password"}).status_code == 200


def test_ey_full_flow_pdf_xml_snapshot_and_separate_listing(ey_client):
    client = ey_client
    files = {"csv_file": ("ey.csv", ey_csv(), "text/csv")}
    preview = client.post("/api/eee-taxi/preview", files=files)
    assert preview.status_code == 200, preview.text
    [review] = preview.json()["rows"]
    assert (review["fare"], review["toll"], review["gst"], review["total"]) == ("322.00", "30.00", "17.60", "369.60")
    assert review["eng_code"] == "E-45303477"
    start = client.post("/api/eee-taxi/batch", files=files, data={"invoice_date": "2026-05-08", "start_suffix": 157, "sign_mode": "dummy"})
    assert start.status_code == 200, start.text
    batch_id = start.json()["batch_id"]
    assert start.json()["first_invoice"] == "HR/HO/26-27/0157"
    # A master change after upload must not affect this batch or its XML.
    with SessionLocal() as db:
        save_ey_rates(db, EyRateCard(p2p_minimum=999, tally_company="EY Test Haryana Company"), "test")
        batch = db.get(EeeTaxiBatch, batch_id)
        assert batch_rows(batch, batch_rates(batch))[0].trip_fare == 322
    gen = client.post(f"/api/eee-taxi/batch/{batch_id}/generate-next")
    assert gen.json()["generated"]["status"] == "done", gen.text
    invoice_id = gen.json()["generated"]["id"]
    history = client.get("/api/eee-taxi/tally/history?client_profile=ey").json()
    history_item = next(i for i in history["invoices"] if i["id"] == invoice_id)
    assert history_item["status"] == "done" and history_item["total"] == "369.60"
    assert history_item["batch_id"] == batch_id
    pdf = client.get(f"/api/eee-taxi/batch/{batch_id}/invoice/{invoice_id}/download")
    reader = PdfReader(io.BytesIO(pdf.content))
    assert len(reader.pages) == 1
    text_pdf = reader.pages[0].extract_text()
    for value in (
        "E-45303477", "220426-NCR-0418", "06AANCA3858Q1ZW", "369.60",
        "Other References", "PICK UP TIME", "DROP TIME", "Toll & Parking",
        "Total Hrs 60 Mins (45*1.5 = 67.5)",
        "Total Kms 10 Km (10*21.00 = 210)",
        "Night Charge = 0", "Total Fare without Tax = 322",
    ):
        assert value in text_pdf
    assert "Toll and Parking" not in text_pdf
    assert "07AANCA3858Q1ZU" not in text_pdf
    assert client.get("/api/eee-taxi/tally/preview?client_profile=pwc").json()["invoices"] == []
    assert len(client.get("/api/eee-taxi/tally/preview?client_profile=ey").json()["invoices"]) == 1
    tally_preview = client.get("/api/eee-taxi/tally/preview?client_profile=ey").json()["invoices"][0]
    assert tally_preview["batch_id"] == batch_id
    response = client.post("/api/eee-taxi/tally/export", json={"invoice_ids": [invoice_id]})
    assert response.status_code == 200, response.text
    xml = ET.fromstring(response.content.decode("utf-16"))
    assert xml.findtext(".//CMPGSTIN") == "06AANCA3858Q1ZW"
    assert xml.findtext(".//BASICPURCHASEORDERNO") == "E-45303477"
    [voucher] = xml.findall(".//VOUCHER")
    assert voucher.attrib["VCHTYPE"] == "TAX INVOICE"
    assert voucher.findtext("VOUCHERTYPENAME") == "TAX INVOICE"
    assert xml.findtext("./BODY/IMPORTDATA/REQUESTDESC/STATICVARIABLES/SVCURRENTCOMPANY") == "EY Test Haryana Company"
    ledger_names = [entry.findtext("LEDGERNAME") for entry in voucher.findall("LEDGERENTRIES.LIST")]
    assert ledger_names == [
        "Ernst & Young LLP", "CAR RENTAL -  LOCAL( 5%)", "Toll and Parking",
        "OUTPUT CGST @ 2.5.%", "OUTPUT SGST @ 2.5%",
    ]
    assert xml.findtext(".//REPORTNAME") == "Vouchers"
    assert not xml.findall(".//CATEGORYALLOCATIONS.LIST")
    assert xml.findtext(".//BASICSHIPDOCUMENTNO") == "220426-NCR-0418"
    assert xml.findtext(".//SVCURRENTCOMPANY") == "EY Test Haryana Company"
    assert set(n.text for n in xml.findall(".//GSTRATE")) == {"2.5", "5"}
    amounts = [Decimal(n.text) for n in xml.findall(".//VOUCHER/LEDGERENTRIES.LIST/AMOUNT")]
    assert amounts == [Decimal("-369.60"), Decimal("322"), Decimal("30"), Decimal("8.80"), Decimal("8.80")]
    assert sum(amounts) == 0


def test_ey_profile_is_detected_from_company_name_even_without_form_choice(ey_client):
    files = {"csv_file": ("ey.csv", ey_csv(), "text/csv")}
    preview = ey_client.post("/api/eee-taxi/preview", files=files)
    assert preview.status_code == 200, preview.text
    calculated = ey_client.post("/api/eee-taxi/calculate", files=files)
    assert calculated.status_code == 200, calculated.text
    assert calculated.headers["X-Client-Profile"] == "ey"


def test_invoice_history_includes_pending_invoices_and_filters_by_client_and_creation_date(ey_client):
    files = {"csv_file": ("ey.csv", ey_csv(), "text/csv")}
    start = ey_client.post("/api/eee-taxi/batch", files=files,
                           data={"invoice_date": "2026-05-08", "start_suffix": 157, "sign_mode": "dummy"})
    assert start.status_code == 200, start.text
    history = ey_client.get("/api/eee-taxi/tally/history?client_profile=ey").json()
    assert history["count"] == 1
    assert (history["page"], history["page_size"], history["pages"]) == (1, 50, 1)
    item = history["invoices"][0]
    assert item["status"] == "pending"
    assert item["invoice_no"] is None
    assert item["route_no"] == "220426-NCR-0418"
    assert item["guest_name"] == "Armaan Goel"
    assert item["total"] == "369.60"
    assert ey_client.get("/api/eee-taxi/tally/history?client_profile=pwc").json()["count"] == 0
    created_date = item["created_at"][:10]
    filtered = ey_client.get(f"/api/eee-taxi/tally/history?created_from={created_date}&created_to={created_date}")
    assert filtered.json()["count"] == 1
    deleted = ey_client.delete(f"/api/eee-taxi/tally/invoices/{item['id']}")
    assert deleted.status_code == 200
    assert ey_client.get("/api/eee-taxi/tally/history?client_profile=ey").json()["count"] == 0


def test_invoice_history_fetches_only_50_records_per_page(ey_client):
    started = ey_client.post(
        "/api/eee-taxi/batch", files={"csv_file": ("ey.csv", ey_csv(), "text/csv")},
        data={"invoice_date": "2026-05-08", "start_suffix": 157, "sign_mode": "dummy"},
    )
    assert started.status_code == 200, started.text
    with SessionLocal() as db:
        batch = db.get(EeeTaxiBatch, started.json()["batch_id"])
        db.add_all([
            EeeTaxiInvoice(batch_id=batch.id, row_index=index, invoice_no=f"HR/HO/26-27/{index:04d}",
                           status=EeeTaxiInvoiceStatus.PENDING)
            for index in range(1, 51)
        ])
        db.commit()

    first = ey_client.get("/api/eee-taxi/tally/history?page=1").json()
    second = ey_client.get("/api/eee-taxi/tally/history?page=2").json()
    assert first["count"] == 51 and len(first["invoices"]) == 50 and first["pages"] == 2
    assert second["count"] == 51 and len(second["invoices"]) == 1 and second["page"] == 2


def test_ey_tally_defaults_upgrade_to_names_from_imported_tally_xml():
    old_defaults = EyRateCard(
        tally_company="", tally_sales_local="CAR RENTAL - LOCAL (5%)",
        tally_toll="Toll & Parking", tally_cgst="OUTPUT CGST @2.5%", tally_sgst="OUTPUT SGST @2.5%",
        tally_sales_interstate="CAR RENTAL - INTERSTATE (5%)", tally_igst="OUTPUT IGST @5%",
    )
    with SessionLocal() as db:
        save_ey_rates(db, old_defaults, "test")
        migrated = get_ey_rates(db)

    assert migrated.tally_company == "EEE-TAXI MOBILITY SOLUTIONS PRIVATE LIMITED( HR)"
    assert migrated.tally_voucher_type == "TAX INVOICE"
    assert migrated.tally_sales_local == "CAR RENTAL -  LOCAL( 5%)"
    assert migrated.tally_toll == "Toll and Parking"
    assert migrated.tally_cgst == "OUTPUT CGST @ 2.5.%"
    assert migrated.tally_sgst == "OUTPUT SGST @ 2.5%"
    assert migrated.tally_sales_interstate == "Car Rental - Interstate"
    assert migrated.tally_igst == "Output Igst @ 5%"
    assert migrated.tally_toll_interstate == "Toll & Parking"


@pytest.mark.parametrize("gstin,expected_ledgers", [
    ("07AAEFE1763C1ZU", ["Ernst & Young LLP", "Car Rental - Interstate", "Toll & Parking", "Output Igst @ 5%"]),
    ("06AAEFE1763C1ZW", ["Ernst & Young LLP", "CAR RENTAL -  LOCAL( 5%)", "Toll and Parking", "OUTPUT CGST @ 2.5.%", "OUTPUT SGST @ 2.5%"]),
])
def test_ey_tally_matches_haryana_exports_without_vehicle_cost_centres(gstin, expected_ledgers):
    rates = EyRateCard()
    _, [row] = parse_ey_csv(ey_csv(**{"Entity Gst": gstin}), rates)
    voucher = voucher_for_row(row, "HR/HO/26-27/1164", date(2026, 10, 7), rates, {})
    xml = ET.fromstring(render_tally_xml([voucher], ey_tally_ledgers(rates)).decode("utf-16"))
    entries = xml.findall(".//VOUCHER/LEDGERENTRIES.LIST")
    assert [e.findtext("LEDGERNAME") for e in entries] == expected_ledgers
    assert sum(Decimal(e.findtext("AMOUNT")) for e in entries) == 0
    assert not xml.findall(".//CATEGORYALLOCATIONS.LIST")
    assert xml.findtext(".//REPORTNAME") == "Vouchers"
    assert xml.findtext(".//CMPGSTIN") == "06AANCA3858Q1ZW"


def test_ey_tally_migration_preserves_custom_ledger_names():
    custom = EyRateCard(tally_sales_interstate="Custom Interstate", tally_igst="Custom IGST",
                        tally_toll="Custom Local Toll", tally_toll_interstate="Custom Interstate Toll")
    with SessionLocal() as db:
        save_ey_rates(db, custom, "test")
        migrated = get_ey_rates(db)
    assert migrated.tally_sales_interstate == "Custom Interstate"
    assert migrated.tally_igst == "Custom IGST"
    assert migrated.tally_toll == "Custom Local Toll"
    assert migrated.tally_toll_interstate == "Custom Interstate Toll"


def test_batch_requires_a_valid_starting_invoice_suffix(ey_client):
    files = {"csv_file": ("ey.csv", ey_csv(), "text/csv")}
    missing = ey_client.post("/api/eee-taxi/batch", files=files,
                             data={"invoice_date": "2026-05-08", "sign_mode": "dummy"})
    assert missing.status_code == 422
    for suffix in (0, 10000):
        response = ey_client.post("/api/eee-taxi/batch", files=files,
                                  data={"invoice_date": "2026-05-08", "start_suffix": suffix, "sign_mode": "dummy"})
        assert response.status_code == 422
