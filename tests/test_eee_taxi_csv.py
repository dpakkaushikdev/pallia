"""CSV upload rules: exact header names, explicit GSTIN, MCD+Parking+Toll, "A+B" kms."""
from decimal import Decimal

import pytest

from app.services.eee_taxi_csv import parse_eee_taxi_csv
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD as RATES

# Same layout as the monthly PwC export (multi-line headers, two "Driver Name" columns).
HEADER = (
    'Date,Dispatcher Name,Driver ID,Driver Name,Driver Cont No,Cab No,DS no/Route No,Entity,Entity Gst,'
    'Company Name,Guest Name,Pickup Address,Pickup Zone,Pick up Time,Drop Location,Drop Zone,Drop Time,'
    'Trip End Date,Trip Duration,Hub Out,Hub In,Start Odometer,End Odometer,"Total \nkms","Dead  \nRun Kms",'
    'Package,"Km/Extra/\nKm Charges","Time Extra/\nTime Charges","Night Charge \n11pm to 5am",Trip Fare,'
    'MCD,Parking,Toll,Driver Hours\n'
)
TRIP_1 = (
    '17/09/2026,Sumit,EEED/1,Tejvir,870,HR55BB4906,GGN-0QRT,Price Waterhouse & Co LLP,06AAEFP1428R1ZW,'
    'PWC,Paras Shah,"Tatvam Villas, Sector 72",Gurgaon,8:15 AM,Gurugram 8 B,Gurgaon,22:45,17/09/2026,14:30,'
    ',,49847,49963,96+20,,1900,594,1600,,4094,100,20,30,15\n'
)
TRIP_P2P = (
    '18/09/2026,Mukul,EEED/2,Vishal,893,HR55AX1267,GGN-OYFS,Price Waterhouse & Co LLP,06AAEFP1428R1ZW,'
    'PWC,Raghav Goel,"Sector 52, Gurugram",Gurgaon,8:15 AM,"Mandi House",Delhi,10:00,18/09/2026,1:45,'
    ',,95925,95967,42,,1050,,,,1050,,,,3\n'
)


def _parse(text: str):
    return parse_eee_taxi_csv(text.encode(), RATES)


def test_monthly_export_layout_parses():
    _, rows = _parse(HEADER + TRIP_1 + TRIP_P2P)
    rental, p2p = rows
    assert rental.car_no == "HR55BB4906"
    assert rental.pickup_location == "Tatvam Villas, Sector 72"
    assert rental.client_gstin == "06AAEFP1428R1ZW"
    assert rental.booking_type == "rental" and p2p.booking_type == "p2p"


def test_toll_is_mcd_plus_parking_plus_toll():
    _, rows = _parse(HEADER + TRIP_1)
    assert rows[0].parking == Decimal("150")


def test_total_kms_with_plus_is_summed():
    _, rows = _parse(HEADER + TRIP_1)
    assert rows[0].total_kms == Decimal("116")
    assert rows[0].billing_kms == Decimal("116")


def test_header_mismatch_names_the_correct_header():
    bad = HEADER.replace("Entity Gst", "GST").replace("Cab No", "Car No")
    with pytest.raises(ValueError) as exc:
        _parse(bad + TRIP_1)
    msg = str(exc.value)
    assert '"GST"' in msg and '"Entity Gst"' in msg
    assert '"Car No"' in msg and '"Cab No"' in msg
    assert "re-upload" in msg


def test_missing_header_without_close_match_is_listed():
    bad = HEADER.replace("Pickup Address,", "Address of guest,")
    with pytest.raises(ValueError, match='"Pickup Address"'):
        _parse(bad + TRIP_1)


def test_blank_gstin_rejects_file_instead_of_guessing():
    row = TRIP_P2P.replace("06AAEFP1428R1ZW", "")
    with pytest.raises(ValueError, match=r"Row 2 .*Entity Gst is blank"):
        _parse(HEADER + row)


def test_unknown_gstin_rejects_file():
    row = TRIP_P2P.replace("06AAEFP1428R1ZW", "06AAAAA0000A1Z5")
    with pytest.raises(ValueError, match="not in the selected client master"):
        _parse(HEADER + row)


def test_entity_name_must_match_gstin_owner():
    row = TRIP_P2P.replace("Price Waterhouse & Co LLP", "PricewaterhouseCoopers Services LLP")
    with pytest.raises(ValueError, match="belongs to PRICE WATERHOUSE & CO LLP"):
        _parse(HEADER + row)
