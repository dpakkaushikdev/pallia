from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.pdf_signer import _find_signature_box as app_find_signature_box
from signing_helper.signer import (
    SigningError,
    _find_signature_box as helper_find_signature_box,
    inspect_signature_box,
)


def _form_pdf(path, include_footer=True):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(449, 508, "TMPVL Authorised sign")
    if include_footer:
        pdf.drawString(392, 202, "For PALLIA TRANS LOGISTICS PRIVATE LTD")
        pdf.drawString(499, 137, "Authorised Signatory")
    pdf.save()


def _scaled_form_pdf(path):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.saveState()
    pdf.transform(0.4875, 0, 0, -0.4875, 28.5, A4[1])
    text = pdf.beginText()
    text.setTextOrigin(700, 1583)
    text.textLine("For PALLIA TRANS LOGISTICS PRIVATE LTD")
    text.setTextOrigin(700, 1658)
    text.textLine("Authorised Signatory")
    pdf.drawText(text)
    pdf.restoreState()
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


def test_accounts_inspection_returns_anchor_box_and_rejects_missing_footer(tmp_path):
    path = tmp_path / "accounts.pdf"
    _form_pdf(path)
    expected = (A4[0] * 0.57, 141.0, A4[0] - 15.0, 198.0)
    assert all(abs(a - b) < 0.1 for a, b in zip(inspect_signature_box(path.read_bytes()), expected))

    path = tmp_path / "accounts-missing-footer.pdf"
    _form_pdf(path, include_footer=False)
    try:
        inspect_signature_box(path.read_bytes())
    except SigningError as exc:
        assert "Could not locate both" in str(exc)
    else:
        raise AssertionError("Accounts inspection must reject PDFs without the Pallia footer anchors")


def test_accounts_placement_composes_scaled_and_flipped_pdf_text_matrices(tmp_path):
    path = tmp_path / "accounts-transformed.pdf"
    _scaled_form_pdf(path)
    expected_y_for = A4[1] - 0.4875 * 1583
    expected_y_auth = A4[1] - 0.4875 * 1658
    expected = (A4[0] * 0.57, expected_y_auth + 4, A4[0] - 15, expected_y_for - 4)
    for box in (app_find_signature_box(path), helper_find_signature_box(path), inspect_signature_box(path.read_bytes())):
        assert all(abs(a - b) < 0.1 for a, b in zip(box, expected))
