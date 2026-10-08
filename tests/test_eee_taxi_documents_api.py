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
    create(client, admin, "pwc", "DS%_100")
    result = client.get(URL, params={"client_profile": "ey", "search": "%_"}, headers=admin).json()
    assert result["total"] == 1
    assert result["entries"][0]["id"] == ey["id"]
