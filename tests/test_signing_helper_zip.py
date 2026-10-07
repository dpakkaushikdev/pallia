import io
import sys
import zipfile
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


def test_sign_zip_signs_each_pdf_and_keeps_other_files(monkeypatch):
    calls = []

    def fake_sign(pdf, pin):
        calls.append((pdf, pin))
        return b"signed:" + pdf

    monkeypatch.setattr(signing_helper_main, "sign_pdf_bytes", fake_sign)
    source = _zip_bytes([("invoices/a.pdf", b"one"), ("invoices/b.PDF", b"two"), ("readme.txt", b"notes")])

    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip", content=source, headers={"X-DSC-PIN": "1234"})

    assert response.status_code == 200
    assert response.headers["x-signed-pdf-count"] == "2"
    assert calls == [(b"one", "1234"), (b"two", "1234")]
    with zipfile.ZipFile(io.BytesIO(response.content)) as result:
        assert result.read("invoices/a.pdf") == b"signed:one"
        assert result.read("invoices/b.PDF") == b"signed:two"
        assert result.read("readme.txt") == b"notes"


def test_sign_zip_rejects_archive_without_pdfs():
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip", content=_zip_bytes([("note.txt", b"text")]), headers={"X-DSC-PIN": "1234"})
    assert response.status_code == 400
    assert "No PDF" in response.json()["detail"]


def test_sign_zip_rejects_path_traversal():
    with TestClient(signing_helper_main.app) as client:
        response = client.post("/sign-zip", content=_zip_bytes([("../outside.pdf", b"data")]), headers={"X-DSC-PIN": "1234"})
    assert response.status_code == 400
    assert "unsafe file path" in response.json()["detail"]
