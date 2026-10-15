from datetime import date

import pytest
from pypdf import PdfReader

from app.services import eee_taxi_pdf
from app.services.eee_taxi_csv import parse_eee_taxi_csv
from app.services.eee_taxi_pipeline import build_invoice_pdf
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD
from tests.test_eee_taxi_tally_api import HEADER, RENTAL


@pytest.mark.parametrize("font,bold", [("Arial", "Arial-Bold"), ("Helvetica", "Helvetica-Bold")])
@pytest.mark.parametrize("route", ["DEL-GUR-4126-M9IB", "DEL-GUR-4126-M9IB-LONGER"])
def test_pwc_header_identifiers_and_reference_date(tmp_path, monkeypatch, font, bold, route):
    if font not in eee_taxi_pdf.pdfmetrics.getRegisteredFontNames():
        pytest.skip("Font unavailable")
    monkeypatch.setattr(eee_taxi_pdf, "_FONT_NORMAL", font)
    monkeypatch.setattr(eee_taxi_pdf, "_FONT_BOLD", bold)
    _, [row] = parse_eee_taxi_csv((HEADER + RENTAL.replace("GGN-0QRT", route)).encode(), DEFAULT_RATE_CARD)
    number = "DL/HO/26-27/1431"
    path, _ = build_invoice_pdf(row, number, date(2026, 10, 14), DEFAULT_RATE_CARD, tmp_path)
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    lines = reader.pages[0].extract_text().splitlines()
    assert lines.count(number) == 2
    assert lines.count(route) == 2  # header and remarks
    ref_index = lines.index(number, lines.index(number) + 1)
    assert lines[ref_index + 1] == "dt. 14-Oct-26"
    assert "Authorised Signatory" in lines
