from datetime import date
from dataclasses import replace

import pytest
from pypdf import PdfReader

from app.services.eee_taxi_signer import _find_signature_box


@pytest.mark.parametrize("profile", ["ey", "pwc"])
def test_total_stays_fixed_and_signature_is_between_footer_labels(tmp_path, profile):
    positions = []
    for long_description in [False, True]:
        path = tmp_path / f"{profile}-{long_description}.pdf"
        if profile == "ey":
            from tests.test_ey_billing import ey_csv
            from app.services.ey_csv import parse_ey_csv
            from app.services.ey_rates import EyRateCard
            from app.services.ey_pdf import generate_ey_pdf
            _, [row] = parse_ey_csv(ey_csv(), EyRateCard())
            generate_ey_pdf(row, "HR/HO/26-27/1", date(2026, 10, 14),
                            tuple("Fare calculation line" for _ in range(8 if long_description else 1)), path)
        else:
            from tests.test_eee_taxi_tally_api import HEADER, RENTAL
            from app.services.eee_taxi_csv import parse_eee_taxi_csv
            from app.services.eee_taxi_pipeline import build_invoice_pdf
            from app.services.eee_taxi_rates import DEFAULT_RATE_CARD
            _, [row] = parse_eee_taxi_csv((HEADER + RENTAL).encode(), DEFAULT_RATE_CARD)
            row = replace(row, guest_name="Guest " * (30 if long_description else 1))
            path, _ = build_invoice_pdf(row, "DL/HO/26-27/1", date(2026, 10, 14), DEFAULT_RATE_CARD, tmp_path / str(long_description))
        reader = PdfReader(path)
        assert len(reader.pages) == 1
        anchors = {}
        def visit(text, cm, tm, font, size):
            y = cm[5] + tm[5]
            if text.strip() == "Total":
                anchors["total"] = y
            if cm[4] + tm[4] > 330:
                if "for EEE" in text or "EEE-TAXI MOBILITY" in text:
                    anchors["company"] = min(anchors.get("company", y), y)
                if "Authorised Signatory" in text:
                    anchors["signature"] = y
        reader.pages[0].extract_text(visitor_text=visit)
        positions.append(anchors["total"])
        _, bottom, _, top = _find_signature_box(path)
        assert anchors["signature"] < bottom < top < anchors["company"]
    assert positions[0] == pytest.approx(positions[1], abs=0.1)
