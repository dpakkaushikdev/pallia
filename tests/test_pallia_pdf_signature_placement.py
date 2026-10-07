from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.pdf_signer import _find_signature_box as app_find_signature_box
from signing_helper.signer import _find_signature_box as helper_find_signature_box


def _form_pdf(path, include_footer=True):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(449, 508, "TMPVL Authorised sign")
    if include_footer:
        pdf.drawString(392, 202, "For PALLIA TRANS LOGISTICS PRIVATE LTD")
        pdf.drawString(499, 137, "Authorised Signatory")
    pdf.save()


def test_both_signers_detect_uppercase_pallia_footer_and_ignore_table_label(tmp_path):
    path = tmp_path / "pallia-form.pdf"
    _form_pdf(path)

    expected = (A4[0] * 0.57, 141, A4[0] - 15, 198)
    for find_box in (app_find_signature_box, helper_find_signature_box):
        box = find_box(path)
        assert all(abs(actual - target) < 0.1 for actual, target in zip(box, expected))


def test_both_signers_use_higher_right_side_fallback_when_footer_is_unreadable(tmp_path):
    path = tmp_path / "pallia-form-no-footer-text.pdf"
    _form_pdf(path, include_footer=False)

    expected = (337, 141, 580, 198)
    assert app_find_signature_box(path) == expected
    assert helper_find_signature_box(path) == expected
