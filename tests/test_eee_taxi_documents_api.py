import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from app.database import SessionLocal
from app.main import app
from app.models import EeeTaxiDocumentEntry

URL = "/api/eee-taxi/documents"


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


def create(client, admin, profile="ey", route="DS-101"):
    response = client.post(URL, json={"client_profile": profile, "route_no": route}, headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def png():
    output = io.BytesIO()
    Image.new("RGB", (40, 20), "blue").save(output, "PNG")
    return output.getvalue()


def upload(client, admin, entry, category="ds", data=None, name="screenshot.png"):
    return client.post(f"{URL}/{entry['id']}/files", data={"category": category},
                       files={"file": (name, data if data is not None else png(), "application/octet-stream")}, headers=admin)


def test_documents_require_eee_permission(client, admin):
    assert client.get(URL).status_code == 401
    client.post("/api/auth/register", json={"email": "document-outsider@test.com", "password": "Password123",
                "role": "user", "permissions": ["others"]}, headers=admin)
    login = client.post("/api/auth/login", json={"email": "document-outsider@test.com", "password": "Password123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.get(URL, headers=headers).status_code == 403
    assert client.post(URL, json={"client_profile": "ey", "route_no": "DS-X"}, headers=headers).status_code == 403
    entry = create(client, admin)
    assert upload(client, headers, entry).status_code == 403


def test_client_categories_and_shared_route_number_stay_separate(client, admin):
    ey = create(client, admin)
    pwc = create(client, admin, "pwc")
    assert ey["id"] != pwc["id"]
    assert ey["categories"] == ["invoice", "ds", "parking", "toll_mcd", "gps", "email_screenshot"]
    assert pwc["categories"] == ["invoice", "ds", "parking", "toll_mcd", "gps"]
    assert upload(client, admin, pwc, "email_screenshot").status_code == 400
    assert create(client, admin, route=" ds-101 ")["id"] == ey["id"]


@pytest.mark.parametrize("body", [{"client_profile": "tata", "route_no": "A"},
                                     {"client_profile": "ey", "route_no": " "},
                                     {"client_profile": "ey", "route_no": "A" * 65}])
def test_invalid_entry_is_rejected(client, admin, body):
    assert client.post(URL, json=body, headers=admin).status_code == 422


def test_screenshot_persists_and_duplicate_paste_is_idempotent(client, admin):
    entry = create(client, admin)
    response = upload(client, admin, entry)
    assert response.status_code == 200, response.text
    doc = response.json()["files"][0]
    assert doc["content_type"] == "image/png"
    assert "ds" not in response.json()["missing"]
    assert len(upload(client, admin, entry).json()["files"]) == 1
    reopened = client.get(f"{URL}/{entry['id']}", headers=admin).json()
    assert reopened["files"][0]["id"] == doc["id"]
    url = f"{URL}/{entry['id']}/files/{doc['id']}"
    assert client.get(url).status_code == 401
    download = client.get(url, headers=admin)
    assert download.content == png()
    assert download.headers["content-type"] == "image/png"
    assert "attachment" in download.headers["content-disposition"]
    assert "inline" in client.get(url + "?inline=true", headers=admin).headers["content-disposition"]


def test_attachment_cannot_be_downloaded_under_another_entry(client, admin):
    ey = create(client, admin)
    pwc = create(client, admin, "pwc")
    file_id = upload(client, admin, ey).json()["files"][0]["id"]
    url = f"{URL}/{pwc['id']}/files/{file_id}"
    assert client.get(url, headers=admin).status_code == 404
    assert client.delete(url, headers=admin).status_code == 404


def test_multiple_receipts_pdf_and_remove(client, admin):
    entry = create(client, admin, route="DS-RECEIPTS")
    output = io.BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=300, height=400); writer.write(output)
    pdf = upload(client, admin, entry, "toll_mcd", output.getvalue(), "../receipt.pdf")
    assert pdf.status_code == 200
    image = upload(client, admin, entry, "toll_mcd")
    assert image.status_code == 200
    assert len(image.json()["files"]) == 2
    assert pdf.json()["files"][0]["filename"] == "receipt.pdf"
    for file in image.json()["files"]:
        removed = client.delete(f"{URL}/{entry['id']}/files/{file['id']}", headers=admin)
        assert removed.status_code == 200
    assert "toll_mcd" in removed.json()["missing"]


def test_bad_or_oversize_files_are_rejected(client, admin):
    entry = create(client, admin)
    assert upload(client, admin, entry, data=b"<html>not an image</html>").status_code == 400
    assert upload(client, admin, entry, data=b"%PDF-broken").status_code == 400
    assert upload(client, admin, entry, data=b"x" * (3 * 1024 * 1024 + 1)).status_code == 413
    assert upload(client, admin, entry, category="unknown").status_code == 400


def test_search_filters_client_and_escapes_wildcards(client, admin):
    ey = create(client, admin, route="DS%_100")
    upload(client, admin, ey)
    assert client.post(f"{URL}/{ey['id']}/save", json={}, headers=admin).status_code == 200
    create(client, admin, "pwc", "DS%_100")
    result = client.get(URL, params={"client_profile": "ey", "search": "%_"}, headers=admin).json()
    assert result["total"] == 1
    assert result["entries"][0]["id"] == ey["id"]


@pytest.fixture
def edit_password(client, admin):
    password = "DocsEdit123"
    assert client.post("/api/eee-taxi/rates/password/reset", json={"new_password": password}, headers=admin).status_code == 200
    return password


def test_save_password_edit_and_audit(client, admin, edit_password):
    entry = create(client, admin, route="SAVE-EDIT")
    assert client.post(f"{URL}/{entry['id']}/save", json={}, headers=admin).status_code == 400
    upload(client, admin, entry)
    assert client.get(URL, params={"search":"SAVE-EDIT"}, headers=admin).json()["total"] == 0
    saved = client.post(f"{URL}/{entry['id']}/save", json={}, headers=admin).json()
    assert saved["status"] == "ready" and saved["edit_protected"]
    assert upload(client, admin, entry, "gps").status_code == 403
    update = {"client_profile":"ey", "route_no":"SAVE-EDIT-NEW", "edit_password":"wrong"}
    assert client.put(f"{URL}/{entry['id']}", json=update, headers=admin).status_code == 403
    update["edit_password"] = edit_password
    edited=client.put(f"{URL}/{entry['id']}", json=update, headers=admin).json()
    assert edited["edited_by"] == "admin@test.com" and edited["edited_at"] and edited["status"] == "draft"
    assert client.post(f"{URL}/{entry['id']}/save", json={"edit_password":edit_password}, headers=admin).status_code == 200
    events=client.get(f"{URL}/{entry['id']}/audit",headers=admin).json()["events"]
    assert "edited" in [event["action"] for event in events]
    assert client.post(f"{URL}/{entry['id']}/delete",json={},headers=admin).status_code == 403


def test_ten_rows_count_and_date_filters(client, admin):
    from datetime import datetime
    for index in range(12):
        entry=create(client, admin, "pwc", f"PAGE-{index:02}")
        upload(client, admin, entry)
        client.post(f"{URL}/{entry['id']}/save",json={},headers=admin).raise_for_status()
        with SessionLocal() as db:
            db.get(EeeTaxiDocumentEntry, entry["id"]).updated_at=datetime(2026,10,8,18,30)
            db.commit()
    params={"search":"PAGE-","client_profile":"pwc","updated_from":"2026-10-09","updated_to":"2026-10-09"}
    first=client.get(URL,params=params,headers=admin).json()
    assert first["total"] == 12 and len(first["entries"]) == 10 and first["total_all"] >= 12
    second=client.get(URL,params={**params,"offset":10},headers=admin).json()
    assert len(second["entries"]) == 2
    assert not set(e["id"] for e in first["entries"]) & set(e["id"] for e in second["entries"])
    assert client.get(URL,params={**params,"updated_to":"2026-10-08"},headers=admin).status_code == 400
    assert client.get(URL,params={"search":"PAGE-","updated_to":"2026-10-08"},headers=admin).json()["total"] == 0


def test_invoice_document_copy_survives_bulk_cleanup(client, admin, edit_password):
    import zipfile
    from datetime import date
    from app.models import EeeTaxiBatch, EeeTaxiInvoice, EeeTaxiInvoiceStatus, EeeTaxiDocumentFile
    from app.services.eee_taxi_documents import attach_documents
    from app.services.eee_taxi_pipeline import build_zip
    ey=create(client,admin,"ey","ZIP-ROUTE")
    pwc=create(client,admin,"pwc","ZIP-ROUTE")
    for entry in [ey,pwc]:
        upload(client,admin,entry)
        client.post(f"{URL}/{entry['id']}/save",json={},headers=admin).raise_for_status()
    with SessionLocal() as db:
        batch=EeeTaxiBatch(invoice_date=date(2026,10,9),start_suffix=1,client_profile="ey",created_by="admin@test.com")
        db.add(batch);db.flush()
        invoice=EeeTaxiInvoice(batch_id=batch.id,row_index=0,route_no=" zip-route ",invoice_no="EEE/001",status=EeeTaxiInvoiceStatus.DONE,signed_pdf_data=b"%PDF-signed-unchanged")
        db.add(invoice);db.flush();attach_documents(invoice,batch,db);db.commit()
        batch_id=batch.id
        assert db.get(EeeTaxiDocumentEntry,ey["id"]).used_revision == 1
        assert db.get(EeeTaxiDocumentEntry,pwc["id"]).used_revision == 0
    body={"ids":[ey["id"],pwc["id"]],"edit_password":edit_password}
    assert client.post(URL+"/bulk-delete",json=body,headers=admin).status_code == 409
    assert client.get(f"{URL}/{ey['id']}",headers=admin).status_code == 200
    assert client.post(URL+"/bulk-delete",json={**body,"ids":[ey["id"]],"edit_password":"wrong"},headers=admin).status_code == 403
    deleted=client.post(URL+"/bulk-delete",json={**body,"ids":[ey["id"]]},headers=admin)
    assert deleted.status_code == 200 and deleted.json()["freed_bytes"] == len(png())
    with SessionLocal() as db:
        assert db.query(EeeTaxiDocumentFile).filter_by(entry_id=ey["id"]).count() == 0
        for with_docs in [False,True]:
            archive=zipfile.ZipFile(io.BytesIO(build_zip(batch_id,db,with_documents=with_docs)))
            assert len(archive.namelist()) == (2 if with_docs else 1)
            pdf_name=next(name for name in archive.namelist() if name.endswith('.pdf'))
            assert archive.read(pdf_name) == b"%PDF-signed-unchanged"
            if with_docs:
                assert archive.read(next(name for name in archive.namelist() if '/documents/' in name)) == png()
        db.delete(db.get(EeeTaxiBatch,batch_id));db.commit()
    assert "deleted" in [event["action"] for event in client.get(f"{URL}/{ey['id']}/audit",headers=admin).json()["events"]]


def test_document_startup_migration_keeps_old_saved_entries(monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    import app.database as database
    engine=create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE eee_taxi_document_entries (id VARCHAR(32) PRIMARY KEY, route_no VARCHAR(64))"))
        connection.execute(text("INSERT INTO eee_taxi_document_entries VALUES ('old', 'DS-OLD')"))
        connection.execute(text("CREATE TABLE eee_taxi_invoices (id VARCHAR(32) PRIMARY KEY)"))
    monkeypatch.setattr(database,"engine",engine)
    database._migrate_existing_db()
    database._migrate_existing_db()
    with engine.connect() as connection:
        row=connection.execute(text("SELECT route_no,is_saved,edit_protected,revision,used_revision FROM eee_taxi_document_entries")).one()
        assert tuple(row) == ('DS-OLD',1,1,1,0)
        assert 'document_zip_data' in {column['name'] for column in inspect(connection).get_columns('eee_taxi_invoices')}
    engine.dispose()


def test_history_thirty_latest_all_statuses_date_filter_and_bulk_delete(client, admin, edit_password):
    from datetime import datetime, timedelta
    from app.models import EeeTaxiDocumentFile
    from hashlib import sha256
    ids=[]
    with SessionLocal() as db:
        for index in range(32):
            entry=EeeTaxiDocumentEntry(client_profile="pwc",route_no=f"HISTORY-{index:02}",created_by="admin@test.com",is_saved=True,edit_protected=True,updated_at=datetime(2026,10,9,6)+timedelta(minutes=index),revision=1,used_revision=1 if index%2 else 0)
            entry.files.append(EeeTaxiDocumentFile(category="ds",filename="ds.png",content_type="image/png",size=len(png()),sha256=sha256(png()).hexdigest(),data=png(),uploaded_by="admin@test.com"))
            db.add(entry);db.flush();ids.append(entry.id)
        db.commit()
    params={"search":"HISTORY-","limit":30,"updated_from":"2026-10-09","updated_to":"2026-10-09"}
    result=client.get(URL,params=params,headers=admin)
    assert result.status_code == 200,result.text
    data=result.json()
    assert data["total"] == 32 and data["page_size"] == 30 and len(data["entries"]) == 30
    assert data["entries"][0]["route_no"] == "HISTORY-31"
    assert {entry["status"] for entry in data["entries"]} == {"ready","used"}
    assert len(client.get(URL,params={**params,"offset":30},headers=admin).json()["entries"]) == 2
    assert client.get(URL,params={**params,"used":True},headers=admin).json()["total"] == 16
    assert client.get(URL,params={**params,"used":False},headers=admin).json()["total"] == 16
    assert client.get(URL,params={**params,"updated_from":"2026-10-10","updated_to":"2026-10-10"},headers=admin).json()["total"] == 0
    body={"ids":ids[-3:],"used_only":False,"edit_password":edit_password}
    assert client.post(URL+"/bulk-delete",json={**body,"edit_password":"wrong"},headers=admin).status_code == 403
    assert client.post(URL+"/bulk-delete",json=body,headers=admin).json()["deleted"] == 3
    assert client.get(URL,params=params,headers=admin).json()["total"] == 29
