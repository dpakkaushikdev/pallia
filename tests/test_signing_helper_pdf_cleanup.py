import io
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "signing_helper"))
from signer import _strip_trailing_non_pdf_data  # noqa: E402


def _one_page_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def test_strips_non_pdf_payload_after_valid_eof():
    source = _one_page_pdf() + (b"appended-payload" * 50_000)
    cleaned = _strip_trailing_non_pdf_data(source)

    assert len(cleaned) < len(source)
    assert cleaned.rstrip().endswith(b"%%EOF")
    assert len(PdfReader(io.BytesIO(cleaned), strict=True).pages) == 1


def test_leaves_normal_pdf_unchanged():
    source = _one_page_pdf()
    assert _strip_trailing_non_pdf_data(source) == source


def test_does_not_trim_unverified_eof_marker():
    source = b"not a pdf %%EOF appended data"
    assert _strip_trailing_non_pdf_data(source) == source
