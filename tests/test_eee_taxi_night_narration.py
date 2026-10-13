from datetime import date

from pypdf import PdfReader

from app.services.eee_taxi_csv import parse_eee_taxi_csv
from app.services.eee_taxi_pipeline import build_invoice_pdf
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD
from app.services.eee_taxi_tally import _description
from tests.test_eee_taxi_tally_api import HEADER, RENTAL


def test_pwc_invoice_and_tally_narration_show_night_amount_without_window(tmp_path):
    csv_bytes = (HEADER + RENTAL.replace("8:15 AM", "11:01 PM")).encode()
    _, [row] = parse_eee_taxi_csv(csv_bytes, DEFAULT_RATE_CARD)

    pdf_path, _ = build_invoice_pdf(row, "DL/HO/26-27/1389", date(2026, 10, 5), DEFAULT_RATE_CARD, tmp_path)
    pdf_text = "".join(page.extract_text() for page in PdfReader(pdf_path).pages)
    assert "Night Charges = 265" in pdf_text
    assert "23:01-05:00" not in pdf_text

    tally_text = " ".join(_description(row, DEFAULT_RATE_CARD))
    assert "Night Charges = 265" in tally_text
    assert "23:01-05:00" not in tally_text
