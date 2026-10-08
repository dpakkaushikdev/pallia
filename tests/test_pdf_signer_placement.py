from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.pdf_signer import _SIG_BOX_FALLBACK, _find_signature_box


def _make_pdf(path, anchors=True):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    if anchors:
        pdf.drawString(360, 160, "For Pallia Trans Logistics Private Limited")
        pdf.drawString(360, 80, "Authorised Signatory")
    pdf.save()


def test_detected_pallia_signature_box_is_in_lower_right(tmp_path):
    path = tmp_path / "pallia.pdf"
    _make_pdf(path)

    x1, y1, x2, y2 = _find_signature_box(path)

    assert x1 > A4[0] / 2
    assert abs(x2 - (A4[0] - 15)) < 0.01
    assert y1 < y2
    assert 80 < y1 < 160


def test_pallia_fallback_signature_box_matches_marked_a4_area(tmp_path):
    path = tmp_path / "no-footer-text.pdf"
    _make_pdf(path, anchors=False)

    assert _find_signature_box(path) == _SIG_BOX_FALLBACK
    assert _SIG_BOX_FALLBACK[0] > A4[0] / 2
    assert _SIG_BOX_FALLBACK[2] <= A4[0]
