import io

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.pdf_signer import _find_signature_box as app_find_signature_box
from signing_helper.signer import (
    SigningError,
    _find_signature_box as helper_find_signature_box,
    inspect_signature_box,
    sign_pdf_bytes,
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


@pytest.mark.parametrize("page_count", [2, 3])
def test_accounts_rejects_multiple_pages_in_review_and_before_token_signing(tmp_path, page_count):
    path = tmp_path / "accounts.pdf"
    _form_pdf(path)
    writer = PdfWriter()
    writer.add_page(PdfReader(path).pages[0])
    for _ in range(page_count - 1):
        writer.add_blank_page(width=A4[0], height=A4[1])
    output = io.BytesIO()
    writer.write(output)
    pdf_bytes = output.getvalue()

    for action in (
        lambda: inspect_signature_box(pdf_bytes),
        lambda: sign_pdf_bytes(pdf_bytes, "unused", sig_box=[1, 2, 3, 4], require_signature_anchors=True),
    ):
        with pytest.raises(SigningError, match=f"This PDF has {page_count} pages"):
            action()


def test_compact_footer_gap_fits_signature_between_both_lines(tmp_path):
    path = tmp_path / "compact-footer.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setFont("Helvetica", 6)
    pdf.drawString(360, 40.69, "For Pallia Trans Logistics Private Limited")
    pdf.drawString(360, 14.25, "Authorised Signatory")
    pdf.save()
    for box in (app_find_signature_box(path), helper_find_signature_box(path, require_anchors=True)):
        assert abs(box[1] - 18.25) < 0.01
        assert abs(box[3] - 36.69) < 0.01
        assert 14.25 < box[1] < box[3] < 40.69


def test_footer_gap_too_small_reports_space_problem_instead_of_missing_lines(tmp_path):
    path = tmp_path / "tiny-footer.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(360, 40, "For Pallia Trans Logistics Private Limited")
    pdf.drawString(360, 25, "Authorised Signatory")
    pdf.save()
    with pytest.raises(SigningError, match="Both footer lines were found"):
        inspect_signature_box(path.read_bytes())


@pytest.mark.parametrize("footer_y", [310.77, 302.85, 220.86])
@pytest.mark.parametrize("company", ["For PALLIA TRANS LOGISTICS PRIVATE LIMITED",
                                     "FOR PALLIA LOGISTICS PRIVATE LIMITED"])
def test_mahindra_landscape_signature_tracks_left_footer(tmp_path, footer_y, company):
    path = tmp_path / "mahindra.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(841.89, 595.28))
    pdf.setFont("Helvetica", 6.6)
    pdf.drawString(9.91, footer_y, company)
    pdf.drawString(9.91, footer_y - 106.77, "Authorised Signatory")
    pdf.drawString(450, 510, "Authorised Signatory")
    pdf.save()
    for find_box in (app_find_signature_box, helper_find_signature_box):
        x1, y1, x2, y2 = find_box(path)
        assert 9.91 < x1 < x2 < 841.89 / 2
        assert y1 == pytest.approx(footer_y - 106.77 + 8)
        assert y2 == pytest.approx(footer_y - 8)


def test_billing_rejects_missing_footer_before_opening_dsc(tmp_path):
    path = tmp_path / "no-footer.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 200, "Invoice without footer anchors")
    pdf.save()
    with pytest.raises(SigningError, match="Could not locate both"):
        sign_pdf_bytes(path.read_bytes(), "unused")
