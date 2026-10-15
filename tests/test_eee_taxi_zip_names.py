import io
import zipfile
from datetime import date

from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import EeeTaxiBatch, EeeTaxiInvoice, EeeTaxiInvoiceStatus
from app.services.eee_taxi_pipeline import build_documents_zip, build_zip


def test_zip_exports_use_ds_number_and_preserve_pdf_content():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    signed_data = output.getvalue()
    invoice_writer = PdfWriter()
    invoice_writer.add_blank_page(width=100, height=100)
    invoice_output = io.BytesIO()
    invoice_writer.write(invoice_output)
    invoice_data = invoice_output.getvalue()
    with Session(engine) as db:
        batch = EeeTaxiBatch(invoice_date=date(2026, 10, 14), start_suffix=1428)
        db.add(batch)
        db.flush()
        db.add(EeeTaxiInvoice(
            batch_id=batch.id, row_index=0, route_no=" DS-12345 ",
            invoice_no="DL/HO/26-27/1428", status=EeeTaxiInvoiceStatus.DONE,
            signed_pdf_path="old-invoice-name_signed.pdf", signed_pdf_data=signed_data,
            signed_invoice_data=invoice_data,
        ))
        db.flush()
        for combined, suffix in [(False, "Invoice"), (True, "combined")]:
            with zipfile.ZipFile(io.BytesIO(build_zip(batch.id, db, with_documents=combined))) as archive:
                assert archive.namelist() == [f"DS-12345_{suffix}.pdf"]
                assert archive.read(archive.namelist()[0]) == (signed_data if combined else invoice_data)
                assert len(PdfReader(io.BytesIO(archive.read(archive.namelist()[0]))).pages) == (2 if combined else 1)
        with zipfile.ZipFile(io.BytesIO(build_documents_zip(batch.id, db))) as archive:
            assert archive.namelist() == ["DS-12345_Doc.pdf"]
            documents = PdfReader(io.BytesIO(archive.read("DS-12345_Doc.pdf")))
            assert len(documents.pages) == 1
            assert documents.pages[0].mediabox.width == 200
    engine.dispose()


def test_legacy_combined_pdf_requires_resigning_for_invoice_only():
    import pytest
    from app.services.eee_taxi_pipeline import invoice_only_signed_bytes, invoice_page_bytes
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    inv = EeeTaxiInvoice(signed_pdf_data=data)
    assert invoice_only_signed_bytes(inv) == data
    assert invoice_page_bytes(data) == data
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    inv.signed_pdf_data = output.getvalue()
    with pytest.raises(ValueError, match="Regenerate and sign"):
        invoice_only_signed_bytes(inv)
    assert len(PdfReader(io.BytesIO(invoice_page_bytes(inv.signed_pdf_data))).pages) == 1
