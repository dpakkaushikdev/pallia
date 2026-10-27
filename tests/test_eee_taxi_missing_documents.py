"""Missing supporting documents on review, and document-name labels in the combined PDF."""
from __future__ import annotations

import io
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.services.eee_taxi_csv import parse_eee_taxi_csv
from app.services.eee_taxi_documents import append_supporting_pages, missing_documents
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD
from tests.test_eee_taxi_tally_api import HEADER, P2P_UNMAPPED_CAR, RENTAL
from tests.test_ey_billing import row as ey_row

ZERO = Decimal("0")


def pwc_rows():
    return parse_eee_taxi_csv((HEADER + RENTAL + P2P_UNMAPPED_CAR).encode(), DEFAULT_RATE_CARD)[1]


def no_charges(trip):
    return replace(trip, toll_amount=ZERO, mcd_amount=ZERO, parking_amount=ZERO)


def test_csv_keeps_toll_mcd_and_parking_separately():
    rental, p2p = pwc_rows()
    assert (rental.mcd_amount, rental.parking_amount, rental.toll_amount) == (100, 20, 30)
    assert rental.parking == 150  # combined passthrough is unchanged
    assert (p2p.mcd_amount, p2p.parking_amount, p2p.toll_amount) == (0, 0, 0)


def test_pwc_rental_needs_ds_gps_and_charge_documents():
    rental, _ = pwc_rows()
    assert missing_documents(rental, "pwc", set()) == ("DS", "Parking", "Toll / MCD", "GPS")
    assert missing_documents(rental, "pwc", {"ds", "gps"}) == ("Parking", "Toll / MCD")
    assert missing_documents(no_charges(rental), "pwc", {"ds"}) == ("GPS",)


def test_pwc_p2p_needs_only_charge_documents():
    _, p2p = pwc_rows()
    assert missing_documents(p2p, "pwc", set()) == ()
    assert missing_documents(replace(p2p, toll_amount=Decimal("50")), "pwc", set()) == ("Toll / MCD",)
    assert missing_documents(replace(p2p, mcd_amount=Decimal("10")), "pwc", set()) == ("Toll / MCD",)
    assert missing_documents(replace(p2p, parking_amount=Decimal("30")), "pwc", set()) == ("Parking",)
    assert missing_documents(replace(p2p, parking_amount=Decimal("30")), "pwc", {"parking"}) == ()


@pytest.mark.parametrize("package", ["P2P", "900"])
def test_ey_needs_ds_gps_and_email_for_p2p_and_rental(package):
    trip = ey_row(**{"Package": package, "MCD": "0", "Parking": "", "Toll": "0"})
    assert missing_documents(trip, "ey", set()) == ("DS", "GPS", "Email Screenshot")
    assert missing_documents(trip, "ey", {"ds", "gps", "email_screenshot"}) == ()
    tolled = ey_row(**{"Package": package, "MCD": "", "Parking": "10", "Toll": "5"})
    assert missing_documents(tolled, "ey", {"ds", "gps"}) == ("Parking", "Toll / MCD", "Email Screenshot")


def _pdf(pages, text):
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=(595, 842))
    for number in range(pages):
        page.drawString(200, 400, f"{text} {number + 1}")
        page.showPage()
    page.save()
    return out.getvalue()


def _png():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(out, "PNG")
    return out.getvalue()


def _file(category, content_type, data, order):
    return SimpleNamespace(category=category, content_type=content_type, data=data,
                           created_at=datetime(2026, 10, 1, 0, order), id=str(order))


def test_combined_pdf_labels_each_document_page_but_not_the_invoice():
    files = [
        _file("email_screenshot", "image/png", _png(), 1),
        _file("toll_mcd", "application/pdf", _pdf(2, "Toll receipt"), 2),
        _file("ds", "image/png", _png(), 3),
        _file("parking", "image/png", _png(), 4),
        _file("gps", "application/pdf", _pdf(1, "GPS map"), 5),
    ]
    combined = PdfReader(io.BytesIO(append_supporting_pages(_pdf(1, "Invoice page"), files)))
    texts = [page.extract_text() or "" for page in combined.pages]
    assert len(texts) == 7
    assert "Invoice page 1" in texts[0]
    assert not any(label in texts[0] for label in ("DS", "Parking", "Toll / MCD", "GPS", "Email Screenshot"))
    expected = ["DS", "Parking", "Toll / MCD", "Toll / MCD", "GPS", "Email Screenshot"]
    for text, label in zip(texts[1:], expected):
        assert label in text
    assert "Toll receipt 1" in texts[3] and "Toll receipt 2" in texts[4]
    assert "GPS map 1" in texts[5]
