"""Invoice PDFs and uploaded documents kept in file storage instead of database columns."""
import io
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import inspect

from app.database import SessionLocal
from app.main import app
from app.models import (EeeTaxiBatch, EeeTaxiDocumentEntry, EeeTaxiDocumentFile, EeeTaxiInvoice,
                        EeeTaxiInvoiceStatus)
from app.services import file_store
from app.services.eee_taxi_files import MAX_IMAGE_SIDE, set_invoice_file, shrink_image
from app.services.file_store import FileStoreError

DOCUMENTS = "/api/eee-taxi/documents"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as client:
        client.post("/api/auth/register", json={"email": "admin@test.com", "password": "Password123"})
        yield client
        with SessionLocal() as db:
            for entry in db.query(EeeTaxiDocumentEntry).all():
                db.delete(entry)
            db.commit()


@pytest.fixture(scope="module")
def admin(client):
    response = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "Password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def storage(monkeypatch):
    """An in-memory stand-in for the storage bucket."""
    files: dict[str, bytes] = {}

    def get(key):
        if key not in files:
            raise FileStoreError(f"{key} is not in storage")
        return files[key]

    def delete(keys):
        for key in keys:
            files.pop(key, None)

    monkeypatch.setattr(file_store, "enabled", lambda: True)
    monkeypatch.setattr(file_store, "put", lambda key, data, content_type: files.__setitem__(key, data))
    monkeypatch.setattr(file_store, "get", get)
    monkeypatch.setattr(file_store, "delete", delete)
    return files


def pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def photo(width, height):
    """A noisy picture, so it compresses like a camera photo and not like a flat colour."""
    output = io.BytesIO()
    Image.effect_noise((width, height), 25).convert("RGB").save(output, "JPEG", quality=95)
    return output.getvalue()


def new_entry(client, admin, route):
    response = client.post(DOCUMENTS, json={"client_profile": "pwc", "route_no": route}, headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, admin, entry, data, name):
    return client.post(f"{DOCUMENTS}/{entry['id']}/files", data={"category": "ds"},
                       files={"file": (name, data, "application/octet-stream")}, headers=admin)


def awaiting_invoice(pdf_bytes=None, route="STORE-1"):
    with SessionLocal() as db:
        batch = EeeTaxiBatch(invoice_date=date(2026, 11, 1), start_suffix=1)
        db.add(batch)
        db.flush()
        invoice = EeeTaxiInvoice(batch_id=batch.id, row_index=0, route_no=route,
                                 status=EeeTaxiInvoiceStatus.AWAITING_SIGNATURE)
        db.add(invoice)
        db.flush()
        if pdf_bytes is not None:
            set_invoice_file(invoice, "pdf", pdf_bytes)
        db.commit()
        return batch.id, invoice.id


def test_uploaded_document_is_kept_in_storage_not_in_the_row(client, admin, storage):
    entry = new_entry(client, admin, "STORE-DOC-1")
    data = pdf()

    body = upload(client, admin, entry, data, "slip.pdf").json()

    file_id = body["files"][0]["id"]
    with SessionLocal() as db:
        row = db.get(EeeTaxiDocumentFile, file_id)
        assert row.data == b""
        assert storage[row.storage_key] == data
    download = client.get(f"{DOCUMENTS}/{entry['id']}/files/{file_id}", headers=admin)
    assert download.content == data
    assert "max-age" in download.headers["Cache-Control"]


def test_removing_a_document_removes_it_from_storage(client, admin, storage):
    entry = new_entry(client, admin, "STORE-DOC-2")
    file_id = upload(client, admin, entry, pdf(), "slip.pdf").json()["files"][0]["id"]
    assert len([key for key in storage if entry["id"] in key]) == 1

    response = client.request("DELETE", f"{DOCUMENTS}/{entry['id']}/files/{file_id}", headers=admin, json={})

    assert response.status_code == 200, response.text
    assert not [key for key in storage if entry["id"] in key]


def test_document_stays_in_the_database_when_storage_is_down(client, admin, storage, monkeypatch):
    def broken(key, data, content_type):
        raise FileStoreError("storage is down")
    monkeypatch.setattr(file_store, "put", broken)
    entry = new_entry(client, admin, "STORE-DOC-3")
    data = pdf()

    body = upload(client, admin, entry, data, "slip.pdf").json()

    with SessionLocal() as db:
        row = db.get(EeeTaxiDocumentFile, body["files"][0]["id"])
        assert row.storage_key is None
        assert row.data == data


def test_large_photo_is_shrunk_to_a_jpeg_and_renamed(client, admin):
    entry = new_entry(client, admin, "STORE-DOC-4")
    original = photo(2400, 1200)
    assert len(original) < 3 * 1024 * 1024

    body = upload(client, admin, entry, original, "toll photo.jpeg").json()

    stored = body["files"][0]
    assert stored["content_type"] == "image/jpeg"
    assert stored["filename"] == "toll photo.jpg"
    assert stored["size"] < len(original)
    assert upload(client, admin, entry, original, "toll photo.jpeg").json()["files"] == body["files"]


def test_shrink_image_caps_the_longest_side_and_keeps_small_images():
    smaller, content_type, suffix = shrink_image(photo(3000, 1000), "image/jpeg", ".jpg")
    with Image.open(io.BytesIO(smaller)) as image:
        assert max(image.size) == MAX_IMAGE_SIDE
    assert (content_type, suffix) == ("image/jpeg", ".jpg")

    tiny = io.BytesIO()
    Image.new("RGB", (40, 20), "blue").save(tiny, "PNG")
    assert shrink_image(tiny.getvalue(), "image/png", ".png") == (tiny.getvalue(), "image/png", ".png")


def test_signing_moves_both_pdfs_to_storage_and_drops_the_unsigned_copy(client, admin, storage):
    unsigned, signed = pdf(), pdf() + b"\n% signed"
    batch_id, invoice_id = awaiting_invoice(unsigned)
    base = f"/api/eee-taxi/batch/{batch_id}"
    assert list(storage.values()) == [unsigned]
    assert client.get(f"{base}/invoice/{invoice_id}/unsigned", headers=admin).content == unsigned

    response = client.post(f"{base}/invoice/{invoice_id}/signed", headers=admin,
                           files={"file": ("signed.pdf", signed, "application/pdf")})

    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        inv = db.get(EeeTaxiInvoice, invoice_id)
        assert inv.pdf_key is None and inv.pdf_data is None
        assert inv.signed_pdf_data is None and inv.signed_invoice_data is None
        assert storage[inv.signed_pdf_key] == signed
        assert storage[inv.signed_invoice_key] == signed
    assert unsigned not in storage.values()
    assert client.get(f"{base}/invoice/{invoice_id}/download", headers=admin).content == signed
    archive = client.get(f"{base}/download-all", headers=admin)
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read(zipped.namelist()[0]) == signed


def test_deleting_an_invoice_removes_its_stored_pdfs(storage):
    from app.services.eee_taxi_tally import delete_invoice
    _, invoice_id = awaiting_invoice(pdf(), route="STORE-2")
    assert storage

    with SessionLocal() as db:
        delete_invoice(db, invoice_id)

    assert not storage


def test_pdf_stays_in_the_database_when_storage_is_down(storage, monkeypatch):
    def broken(key, data, content_type):
        raise FileStoreError("storage is down")
    monkeypatch.setattr(file_store, "put", broken)
    data = pdf()

    _, invoice_id = awaiting_invoice(data, route="STORE-3")

    with SessionLocal() as db:
        inv = db.get(EeeTaxiInvoice, invoice_id)
        assert inv.pdf_key is None
        assert inv.pdf_data == data


def test_batch_status_does_not_read_any_pdf(client, admin):
    """Storage off: the PDF sits in the row, and a status check must still leave it unread."""
    batch_id, invoice_id = awaiting_invoice(pdf(), route="STORE-4")
    with SessionLocal() as db:
        inv = db.get(EeeTaxiInvoice, invoice_id)
        inv.status = EeeTaxiInvoiceStatus.DONE
        inv.signed_pdf_data, inv.pdf_data = inv.pdf_data, None
        db.commit()

    body = client.get(f"/api/eee-taxi/batch/{batch_id}", headers=admin).json()

    assert body["invoices"][0]["has_signed_pdf"] is True
    with SessionLocal() as db:
        inv = db.query(EeeTaxiInvoice).filter(EeeTaxiInvoice.id == invoice_id).one()
        batch = db.get(EeeTaxiBatch, batch_id)
        assert {"pdf_data", "signed_pdf_data", "signed_invoice_data"} <= inspect(inv).unloaded
        assert {"csv_data", "rates_snapshot", "client_master_snapshot"} <= inspect(batch).unloaded


def test_generated_invoice_with_a_stored_document_goes_to_storage(client, admin, storage):
    from pypdf import PdfReader
    from app.models import EeeTaxiCostCentre
    from tests.test_eee_taxi_review_api import _clear, _files
    _clear()
    with SessionLocal() as db:
        db.add(EeeTaxiCostCentre(vehicle_no="HR55BB4906", cost_centre="HR55BB4906"))
        db.commit()
    try:
        entry = new_entry(client, admin, "GGN-OYFS")
        assert upload(client, admin, entry, pdf(), "slip.pdf").status_code == 200
        saved = client.post(f"{DOCUMENTS}/{entry['id']}/save", json={}, headers=admin)
        assert saved.status_code == 200, saved.text
        files, data = _files({"exclude_rows": "0"})
        batch_id = client.post("/api/eee-taxi/batch", files=files, data=data, headers=admin).json()["batch_id"]

        generated = client.post(f"/api/eee-taxi/batch/{batch_id}/generate-next", headers=admin).json()

        assert generated["generated"]["status"] == "done", generated
        with SessionLocal() as db:
            [inv] = db.query(EeeTaxiInvoice).filter(EeeTaxiInvoice.batch_id == batch_id).all()
            assert inv.pdf_data is None and inv.signed_pdf_data is None and inv.signed_invoice_data is None
            assert inv.pdf_key is None
            assert len(PdfReader(io.BytesIO(storage[inv.signed_pdf_key])).pages) == 2
            assert len(PdfReader(io.BytesIO(storage[inv.signed_invoice_key])).pages) == 1
    finally:
        _clear()


def test_storage_request_is_retried_after_a_passing_server_error(monkeypatch):
    import httpx
    answers = [httpx.Response(520), httpx.Response(200, content=b"file")]

    class Flaky:
        def request(self, method, url, **kwargs):
            response = answers.pop(0)
            response.request = httpx.Request(method, url)
            return response

    monkeypatch.setattr(file_store, "_http", lambda: Flaky())
    monkeypatch.setattr(file_store, "RETRY_WAIT_SECONDS", 0)

    assert file_store.get("invoices/a.pdf") == b"file"
    assert answers == []
