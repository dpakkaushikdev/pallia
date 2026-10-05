"""The rupee sign in the invoice total must render on servers without Windows fonts."""
from __future__ import annotations

from datetime import date

from app.services import eee_taxi_pdf
from app.services.eee_taxi_csv import parse_eee_taxi_csv
from app.services.eee_taxi_pipeline import build_invoice_pdf
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD
from tests.test_eee_taxi_tally_api import HEADER, RENTAL


def test_total_rupee_sign_uses_the_bundled_font_when_arial_is_missing(tmp_path, monkeypatch):
    # Vercel (Linux) has no C:\Windows\Fonts, so the PDF falls back to Helvetica.
    monkeypatch.setattr(eee_taxi_pdf, "_FONT_NORMAL", "Helvetica")
    monkeypatch.setattr(eee_taxi_pdf, "_FONT_BOLD", "Helvetica-Bold")
    _, [row] = parse_eee_taxi_csv((HEADER + RENTAL).encode(), DEFAULT_RATE_CARD)

    pdf_path, _ = build_invoice_pdf(row, "DL/HO/26-27/1388", date(2026, 10, 5), DEFAULT_RATE_CARD, tmp_path)

    data = pdf_path.read_bytes()
    assert b"DejaVuSans-Bold" in data  # the sign is drawn with the embedded font, not Helvetica
    from pypdf import PdfReader
    text = "".join(page.extract_text() for page in PdfReader(pdf_path).pages)
    assert "₹" in text
