import io
import base64
import sys
import zipfile
import pytest
from reportlab.pdfgen import canvas
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "signing_helper"))
import main as signing_helper_main  # noqa: E402


def _zip_bytes(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return data.getvalue()


def test_inspect_zip_lists_invoice_pdfs_before_signing():
    source = _zip_bytes([("invoices/a.pdf", b"one"), ("invoices/b.PDF", b"two"), ("readme.txt", b"notes")])
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip/inspect", content=source)
    assert response.status_code == 200
    assert response.json() == {
        "files": [
            {"filename": "invoices/a.pdf", "status": "Ready"},
            {"filename": "invoices/b.PDF", "status": "Ready"},
        ],
        "count": 2,
    }


def test_sign_zip_tracks_each_pdf_and_keeps_other_files(monkeypatch):
    calls = []

    def fake_sign(pdf, pin):
        calls.append((pdf, pin))
        return b"signed:" + pdf

    monkeypatch.setattr(signing_helper_main, "sign_pdf_bytes", fake_sign)
    source = _zip_bytes([("invoices/a.pdf", b"one"), ("invoices/b.PDF", b"two"), ("readme.txt", b"notes")])

    with TestClient(signing_helper_main.app) as client:
        inspected = client.post("/sign-zip/inspect", content=source)
        response = client.post("/sign-zip", content=source, headers={"X-DSC-PIN": "1234"})

    assert inspected.status_code == 200
    assert response.status_code == 202
    assert calls == [(b"one", "1234"), (b"two", "1234")]
    job_id = response.json()["job_id"]
    with TestClient(signing_helper_main.app) as client:
        status = client.get(f"/sign-zip/{job_id}")
        result = client.get(f"/sign-zip/{job_id}/download")
    assert status.json()["state"] == "complete"
    assert [item["status"] for item in status.json()["files"]] == ["Signed", "Signed"]
    assert result.headers["x-signed-pdf-count"] == "2"
    with zipfile.ZipFile(io.BytesIO(result.content)) as signed:
        assert signed.read("invoices/a.pdf") == b"signed:one"
        assert signed.read("invoices/b.PDF") == b"signed:two"
        assert signed.read("readme.txt") == b"notes"


def test_sign_zip_rejects_archive_without_pdfs():
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip/inspect", content=_zip_bytes([("note.txt", b"text")]))
    assert response.status_code == 400
    assert "No PDF" in response.json()["detail"]


def test_sign_zip_rejects_path_traversal():
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip/inspect", content=_zip_bytes([("../outside.pdf", b"data")]))
    assert response.status_code == 400
    assert "unsafe file path" in response.json()["detail"]


def test_sign_zip_reports_per_invoice_failure(monkeypatch):
    def fail_sign(_pdf, _pin):
        raise signing_helper_main.WrongPIN("Incorrect PIN.")

    monkeypatch.setattr(signing_helper_main, "sign_pdf_bytes", fail_sign)
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip", content=_zip_bytes([("invoice.pdf", b"pdf")]), headers={"X-DSC-PIN": "bad"})
        job = client.get(f"/sign-zip/{response.json()['job_id']}")
        download = client.get(f"/sign-zip/{response.json()['job_id']}/download")

    assert response.status_code == 202
    assert job.json()["state"] == "failed"
    assert job.json()["files"] == [{"filename": "invoice.pdf", "status": "Failed", "error": "Incorrect PIN."}]
    assert download.status_code == 409


def _accounts_pdf(pages=1, footer=True):
    data = io.BytesIO()
    pdf = canvas.Canvas(data)
    for _ in range(pages):
        pdf.drawString(50, 600, "Accounts statement")
        if footer:
            pdf.drawString(392, 202, "For PALLIA TRANS LOGISTICS PRIVATE LTD")
            pdf.drawString(499, 137, "Authorised Signatory")
        pdf.showPage()
    pdf.save()
    return data.getvalue()


def test_accounts_zip_checks_each_pdf_and_only_returns_ready_pdf_contents():
    single = _accounts_pdf()
    source = _zip_bytes([
        ("accounts/ready.pdf", single),
        ("accounts/two-pages.pdf", _accounts_pdf(pages=2)),
        ("missing-footer.pdf", _accounts_pdf(footer=False)),
        ("readme.txt", b"notes"),
    ])
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/accounts-zip/inspect", content=source)
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 3
    ready, multiple, missing = result["files"]
    assert ready["filename"] == "accounts/ready.pdf"
    assert ready["status"] == "Ready"
    assert len(ready["signature_box"]) == 4
    assert base64.b64decode(ready["pdf_b64"]) == single
    assert multiple["status"] == "Failed" and "2 pages" in multiple["error"]
    assert missing["status"] == "Failed" and "Could not locate both" in missing["error"]
    assert "pdf_b64" not in multiple and "pdf_b64" not in missing


@pytest.mark.parametrize("source,reason", [
    (_zip_bytes([("../outside.pdf", b"pdf")]), "unsafe file path"),
    (_zip_bytes([("readme.txt", b"notes")]), "No PDF"),
    (_zip_bytes([(f"{i}.pdf", b"pdf") for i in range(51)]), "no more than 50 PDFs"),
])
def test_accounts_zip_rejects_unsafe_empty_and_oversized_pdf_lists(source, reason):
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/accounts-zip/inspect", content=source)
    assert response.status_code == 400
    assert reason in response.json()["detail"]
