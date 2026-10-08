"""Vehicle CSV validation, protected edits, and PDF/Tally make consistency."""
import io
from datetime import date
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiVehicleMaster, EeeTaxiRateCard
from app.services.eee_taxi_pipeline import build_invoice_pdf
from app.services.eee_taxi_tally import voucher_for_row
from app.services.eee_taxi_vehicles import parse_vehicle_csv, vehicle_make_map, vehicle_display_name
from app.services.ey_csv import parse_ey_csv
from app.services.ey_rates import EyRateCard
from app.services.ey_tally import ey_tally_ledgers
from app.services.tally_export import render_tally_xml
from tests.test_ey_billing import ey_csv

CSV = b'Vehicle Number Plate,Make,Availability\nhr 55 ax-3523,TIGOR- XR,Available\n'


def test_csv_normalizes_registration_and_preserves_make():
    [row] = parse_vehicle_csv(CSV)
    assert row.vehicle_no == "HR55AX3523"
    assert row.make == "TIGOR- XR"
    assert vehicle_display_name("hr 55 ax-3523", {row.vehicle_no: row.make}) == "HR55AX3523(TIGOR- XR)"


@pytest.mark.parametrize("source", [
    b'Car,Make\nHR55AX3523,TIGOR\n',
    CSV + b'HR55AX3523,Other Make,Available\n',
    b'Vehicle Number Plate,Make\nHR55AX3523,\n',
])
def test_rejects_invalid_headers_duplicate_registrations_and_missing_make(source):
    with pytest.raises(ValueError):
        parse_vehicle_csv(source)


def test_bundled_master_and_unknown_vehicle():
    master = vehicle_make_map()
    assert len(master) == 90
    assert master["HR55AX3523"] == "TIGOR-EV"
    assert vehicle_display_name("XX99ZZ9999", master) == "XX99ZZ9999"


def test_master_make_is_used_in_pdf_and_tally_even_when_cost_centre_differs(tmp_path):
    rates = EyRateCard()
    _, [row] = parse_ey_csv(ey_csv(**{"Cab No": "HR55AX3523"}), rates)
    master = vehicle_make_map()
    centres = {row.car_no: "HR55AX3523(OLD-MAKE)"}
    path, _ = build_invoice_pdf(row, "HR/HO/26-27/1164", date(2026, 10, 8), rates, tmp_path,
                                cost_centres=centres, vehicle_master=master)
    text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
    assert "HR55AX3523(TIGOR-EV)" in text
    voucher = voucher_for_row(row, "HR/HO/26-27/1164", date(2026, 10, 8), rates, centres,
                              vehicle_master=master)
    xml = ET.fromstring(render_tally_xml([voucher], ey_tally_ledgers(rates)).decode("utf-16"))
    assert xml.findtext(".//BASICSHIPVESSELNO") == "HR55AX3523(TIGOR-EV)"
    assert voucher.car_no == "HR55AX3523"
    assert "OLD-MAKE" not in text


def test_vehicle_master_requires_login_and_password_and_import_is_review_only():
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.query(EeeTaxiVehicleMaster).delete()
            db.query(EeeTaxiRateCard).delete()
            db.commit()
        assert client.get('/api/eee-taxi/vehicles').status_code == 401
        client.post('/api/auth/register', json={'email': 'vehicles@test.com', 'password': 'Password123'})
        login = client.post('/api/auth/login', json={'email': 'vehicles@test.com', 'password': 'Password123'})
        headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
        endpoint = '/api/eee-taxi/vehicles'
        assert len(client.get(endpoint, headers=headers).json()['rows']) == 90
        preview = client.post(endpoint + '/import-csv', files={'file': ('vehicles.csv', CSV, 'text/csv')}, headers=headers)
        assert preview.status_code == 200
        assert len(client.get(endpoint, headers=headers).json()['rows']) == 90
        rows = preview.json()['rows']
        client.post('/api/eee-taxi/rates/password', json={'new_password': 'VehicleEdit1'}, headers=headers)
        assert client.put(endpoint, json={'rows': rows, 'edit_password': 'incorrect'}, headers=headers).status_code == 403
        saved = client.put(endpoint, json={'rows': rows, 'edit_password': 'VehicleEdit1'}, headers=headers)
        assert saved.status_code == 200
        expected = [dict(rows[0], make='TIGOR-EV')]
        assert saved.json()['rows'] == expected
        with SessionLocal() as db:
            assert vehicle_make_map(db) == {'HR55AX3523': 'TIGOR-EV'}
            db.query(EeeTaxiVehicleMaster).delete()
            db.query(EeeTaxiRateCard).delete()
            db.commit()
