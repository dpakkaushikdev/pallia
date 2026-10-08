"""Pallia Trans Local Signing Helper.

Runs silently as a Windows system tray app on http://127.0.0.1:7777.
Auto-registers in Windows startup on first launch (no admin rights needed).

Right-click the tray icon to Stop.
"""
from __future__ import annotations

import base64
import io
import os
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

import uvicorn
from loguru import logger
from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from signer import CertificateSelectionError, SigningError, TokenNotFound, WrongPIN, inspect_signature_box, sign_pdf_bytes

PORT = 7777
VERSION = "1.12.0"   # 1.12: plain signature appearance with a two-line signer name
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 500
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_zip_jobs: dict[str, dict] = {}
_zip_jobs_lock = threading.Lock()

# The .exe runs without a console, so problems go to a log file next to the
# user's local app data: %LOCALAPPDATA%\PalliaSignHelper\helper.log
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PalliaSignHelper"


def _setup_logging() -> None:
    # PyInstaller --noconsole leaves sys.stdout / sys.stderr as None, which
    # breaks anything that calls .isatty() or .write() on them (uvicorn's
    # default log formatter does). Point them at /dev/null instead.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(LOG_DIR / "helper.log", rotation="2 MB", retention=3, level="INFO",
                   enqueue=True, backtrace=False, diagnose=False)
    except Exception:
        pass    # logging must never stop the helper from starting

app = FastAPI(title="Pallia Trans Signing Helper", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pallia-eee-billing.vercel.app",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── API models ────────────────────────────────────────────────────────────────

class SignRequest(BaseModel):
    pdf_b64: str
    pin: str
    # Optional (x1, y1, x2, y2) in PDF points; EEE-Taxi sends this so the
    # stamp lands in its invoice footer instead of being auto-detected.
    sig_box: Optional[list[float]] = None
    require_signature_anchors: bool = False
    token_serial: Optional[str] = None


class InspectPdfRequest(BaseModel):
    pdf_b64: str


class SignResponse(BaseModel):
    signed_pdf_b64: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Pallia Trans Signing Helper",
        "port": PORT,
        "version": VERSION,
        "supports_sig_box": True,
        "supports_token_selection": True,
    }


@app.get("/tokens")
def list_tokens():
    from token_device import connected_tokens, token_serial
    from signer import PKCS11_LIB
    try:
        return {"tokens": [{"serial": token_serial(token), "label": token.label.strip()}
                           for token in connected_tokens(PKCS11_LIB)]}
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "Cannot read DSC devices. Check the USB connection and token driver."})


@app.post("/sign", response_model=SignResponse)
def sign(body: SignRequest):
    try:
        pdf_bytes = base64.b64decode(body.pdf_b64)
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid base64 PDF data."})

    try:
        signed_bytes = sign_pdf_bytes(
            pdf_bytes, body.pin, sig_box=body.sig_box,
            require_signature_anchors=body.require_signature_anchors,
            **({"token_serial": body.token_serial} if body.token_serial else {}),
        )
    except TokenNotFound as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
    except WrongPIN as exc:
        return JSONResponse(status_code=401, content={"detail": str(exc)})
    except CertificateSelectionError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except SigningError as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return SignResponse(signed_pdf_b64=base64.b64encode(signed_bytes).decode())


@app.post("/inspect-pdf")
def inspect_pdf(body: InspectPdfRequest):
    try:
        pdf_bytes = base64.b64decode(body.pdf_b64, validate=True)
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid base64 PDF data."})
    try:
        box = inspect_signature_box(pdf_bytes)
    except SigningError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"signature_box": list(box)}


async def _read_zip_request(request: Request) -> bytes:
    archive_data = bytearray()
    async for chunk in request.stream():
        archive_data.extend(chunk)
        if len(archive_data) > MAX_ZIP_BYTES:
            raise ValueError("ZIP must be no larger than 100 MB.")
    archive_bytes = bytes(archive_data)
    if not archive_bytes:
        raise ValueError("ZIP is empty.")
    return archive_bytes


def _zip_pdf_manifest(archive_bytes: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
            entries = source.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ValueError("ZIP cannot contain more than 500 files.")
            if sum(item.file_size for item in entries) > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("Uncompressed ZIP contents cannot exceed 200 MB.")
            seen: set[str] = set()
            for item in entries:
                name = item.filename.replace("\\", "/")
                parts = name.split("/")
                mode = item.external_attr >> 16
                if name.startswith("/") or (parts and ":" in parts[0]) or any(part in (".", "..") for part in parts) or (mode & 0o170000) == 0o120000:
                    raise ValueError("ZIP contains an unsafe file path.")
                if name in seen:
                    raise ValueError("ZIP contains duplicate file names.")
                seen.add(name)
                if item.flag_bits & 0x1:
                    raise ValueError("Password-protected ZIP files are not supported.")
            pdf_entries = [item for item in entries if not item.is_dir() and item.filename.lower().endswith(".pdf")]
            if not pdf_entries:
                raise ValueError("No PDF invoices were found in the ZIP.")
            return [item.filename for item in pdf_entries]
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a valid ZIP archive.") from exc


def _set_zip_job_file(job_id: str, index: int, status: str, error: str | None = None) -> None:
    with _zip_jobs_lock:
        job = _zip_jobs.get(job_id)
        if job is None:
            return
        job["files"][index]["status"] = status
        if error:
            job["files"][index]["error"] = error


def _process_zip_job(job_id: str, archive_bytes: bytes, pin: str, accounts: bool = False, token_serial: str | None = None) -> None:
    job = _zip_jobs[job_id]
    stop_error = None
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
            pdf_indexes = {name: index for index, name in enumerate(job["pdf_names"])}
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as signed_archive:
                for item in source.infolist():
                    if item.is_dir():
                        signed_archive.writestr(item, b"")
                        continue
                    if item.filename in pdf_indexes:
                        index = pdf_indexes[item.filename]
                        if accounts:
                            content = source.read(item)
                            try:
                                # Validate even after a token failure so skipped
                                # multi-page PDFs retain their specific reason.
                                inspect_signature_box(content)
                                if stop_error:
                                    raise SigningError(f"Signing stopped: {stop_error}")
                                _set_zip_job_file(job_id, index, "Signing")
                                content = sign_pdf_bytes(content, pin, require_signature_anchors=True, **({"token_serial": token_serial} if token_serial else {}))
                            except (TokenNotFound, WrongPIN, CertificateSelectionError) as exc:
                                stop_error = str(exc)
                                _set_zip_job_file(job_id, index, "Failed", stop_error)
                            except SigningError as exc:
                                _set_zip_job_file(job_id, index, "Failed", str(exc))
                            else:
                                _set_zip_job_file(job_id, index, "Signed")
                        else:
                            _set_zip_job_file(job_id, index, "Signing")
                            content = sign_pdf_bytes(source.read(item), pin, **({"token_serial": token_serial} if token_serial else {}))
                            _set_zip_job_file(job_id, index, "Signed")
                    else:
                        content = source.read(item)
                    signed_archive.writestr(item, content)
                if accounts:
                    files = job["files"]
                    signed_count = sum(item["status"] == "Signed" for item in files)
                    unsigned = [item for item in files if item["status"] != "Signed"]
                    report = [f"{signed_count} PDFs signed. {len(unsigned)} PDFs not signed.",
                              "Unsigned PDFs remain unchanged in this ZIP."]
                    report += [f"{item['filename']}: {item.get('error', 'Not processed')}" for item in unsigned]
                    report_name = "accounts-signing-summary.txt"
                    suffix = 2
                    while report_name in source.namelist():
                        report_name = f"accounts-signing-summary-{suffix}.txt"
                        suffix += 1
                    signed_archive.writestr(report_name, "\n".join(report))
        with _zip_jobs_lock:
            job = _zip_jobs.get(job_id)
            if job:
                job["result"] = output.getvalue()
                job["state"] = "complete"
                if accounts:
                    job["signing_stopped"] = stop_error
                job["finished_at"] = time.time()
    except TokenNotFound as exc:
        error = str(exc)
    except WrongPIN as exc:
        error = str(exc)
    except SigningError as exc:
        error = str(exc)
    except Exception as exc:
        logger.exception("ZIP signing job {} failed", job_id)
        error = f"Could not process invoice ZIP: {exc}"
    else:
        return
    with _zip_jobs_lock:
        job = _zip_jobs.get(job_id)
        if job:
            active = next((i for i, item in enumerate(job["files"]) if item["status"] == "Signing"), None)
            if active is not None:
                job["files"][active]["status"] = "Failed"
                job["files"][active]["error"] = error
            job["state"] = "failed"
            job["error"] = error
            job["finished_at"] = time.time()


@app.post("/sign-zip/inspect")
async def inspect_sign_zip(request: Request):
    try:
        archive_bytes = await _read_zip_request(request)
        files = _zip_pdf_manifest(archive_bytes)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"files": [{"filename": name, "status": "Ready"} for name in files], "count": len(files)}


@app.post("/accounts-zip/inspect")
async def inspect_accounts_zip(request: Request, x_manifest_only: str = Header(default="")):
    """Unpack Accounts PDFs for individual review; reject unsafe archives."""
    try:
        archive_bytes = await _read_zip_request(request)
        pdf_names = _zip_pdf_manifest(archive_bytes)
        if len(pdf_names) > 50:
            raise ValueError("Select an Accounts ZIP containing no more than 50 PDFs.")
        files = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
            pdf_bytes_total = sum(source.getinfo(name).file_size for name in pdf_names)
            if pdf_bytes_total > MAX_ZIP_BYTES:
                raise ValueError("Accounts PDFs inside the ZIP cannot exceed 100 MB total.")
            for name in pdf_names:
                item = {"filename": name, "status": "Failed"}
                try:
                    pdf_bytes = source.read(name)
                    box = inspect_signature_box(pdf_bytes)
                    item.update(status="Ready", signature_box=list(box))
                    if x_manifest_only != "true":
                        item["pdf_b64"] = base64.b64encode(pdf_bytes).decode()
                except (SigningError, RuntimeError, ValueError, zipfile.BadZipFile, NotImplementedError) as exc:
                    item["error"] = str(exc)
                files.append(item)
    except (ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"files": files, "count": len(files), "pdf_bytes_total": pdf_bytes_total}


@app.post("/sign-zip")
async def sign_zip(request: Request, background_tasks: BackgroundTasks, x_dsc_pin: str = Header(default=""), x_dsc_token: str = Header(default="")):
    """Start local batch signing; status is available per invoice while it runs."""
    return await _start_zip_job(request, background_tasks, x_dsc_pin, token_serial=x_dsc_token)


@app.post("/accounts-zip")
async def sign_accounts_zip(request: Request, background_tasks: BackgroundTasks, x_dsc_pin: str = Header(default=""), x_dsc_token: str = Header(default="")):
    return await _start_zip_job(request, background_tasks, x_dsc_pin, accounts=True, token_serial=x_dsc_token)


async def _start_zip_job(request: Request, background_tasks: BackgroundTasks, x_dsc_pin: str, accounts: bool = False, token_serial: str | None = None):
    if not x_dsc_pin:
        return JSONResponse(status_code=400, content={"detail": "Enter the DSC token PIN."})
    try:
        archive_bytes = await _read_zip_request(request)
        pdf_names = _zip_pdf_manifest(archive_bytes)
        if accounts:
            if len(pdf_names) > 50:
                raise ValueError("Select an Accounts ZIP containing no more than 50 PDFs.")
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
                if sum(source.getinfo(name).file_size for name in pdf_names) > MAX_ZIP_BYTES:
                    raise ValueError("Accounts PDFs inside the ZIP cannot exceed 100 MB total.")
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    now = time.time()
    with _zip_jobs_lock:
        expired = [
            key for key, value in _zip_jobs.items()
            if value.get("state") in {"complete", "failed"} and now - value.get("created_at", now) > 3600
        ]
        for key in expired:
            _zip_jobs.pop(key, None)
        if len(_zip_jobs) >= 5:
            return JSONResponse(status_code=429, content={"detail": "Finish or download an existing ZIP signing job first."})
        job_id = uuid.uuid4().hex
        _zip_jobs[job_id] = {
            "state": "running", "files": [{"filename": name, "status": "Ready"} for name in pdf_names],
            "pdf_names": pdf_names, "result": None, "created_at": now,
        }
    if accounts:
        background_tasks.add_task(_process_zip_job, job_id, archive_bytes, x_dsc_pin, True, token_serial)
    else:
        background_tasks.add_task(_process_zip_job, job_id, archive_bytes, x_dsc_pin, False, token_serial)
    return JSONResponse(status_code=202, content={"job_id": job_id, "state": "running"})


@app.get("/sign-zip/{job_id}")
def sign_zip_status(job_id: str):
    with _zip_jobs_lock:
        job = _zip_jobs.get(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "ZIP signing job expired or was not found."})
        result = {"state": job["state"], "files": [dict(item) for item in job["files"]], "error": job.get("error")}
        if "signing_stopped" in job:
            result["signing_stopped"] = job["signing_stopped"]
        return result


@app.get("/sign-zip/{job_id}/download")
def download_signed_zip(job_id: str):
    with _zip_jobs_lock:
        job = _zip_jobs.get(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "ZIP signing job expired or was not found."})
        if job["state"] != "complete":
            return JSONResponse(status_code=409, content={"detail": "ZIP signing must finish before download."})
        archive_bytes = job["result"]
        count = sum(item["status"] == "Signed" for item in job["files"])
        unsigned_count = len(job["files"]) - count
        _zip_jobs.pop(job_id, None)
    headers = {"Content-Disposition": 'attachment; filename="pallia_invoices_signed.zip"', "X-Signed-PDF-Count": str(count), "X-Unsigned-PDF-Count": str(unsigned_count)}
    return Response(archive_bytes, media_type="application/zip", headers=headers)


# ── Windows startup registration ──────────────────────────────────────────────

def _register_startup() -> None:
    """Add this .exe to HKCU startup so it launches on every Windows login.

    Uses HKCU (current user) — no administrator rights required.
    Safe to call on every launch; just overwrites the same key.
    """
    try:
        import winreg
        exe_path = sys.executable          # correct path when frozen by PyInstaller
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "PalliaSignHelper", 0, winreg.REG_SZ, f'"{exe_path}"')
        winreg.CloseKey(key)
    except Exception:
        pass    # non-fatal — user can start manually if registry fails


# ── Tray icon ─────────────────────────────────────────────────────────────────

def _make_icon_image():
    """Purple circle with white 'P' — matches the app's accent colour."""
    from PIL import Image, ImageDraw, ImageFont
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.ellipse([2, 2, size - 2, size - 2], fill=(124, 106, 242, 255))

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
    except Exception:
        font = ImageFont.load_default()

    try:
        bb = draw.textbbox((0, 0), "P", font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    except AttributeError:
        tw, th = 20, 28

    draw.text(((size - tw) // 2, (size - th) // 2 - 2), "P",
              font=font, fill=(255, 255, 255, 255))
    return img


def _run_server() -> None:
    try:
        logger.info("Signing helper v{} listening on http://127.0.0.1:{}", VERSION, PORT)
        # log_config=None: skip uvicorn's dictConfig, which needs a console.
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None)
    except Exception:
        # A silent dead server thread is the worst failure mode for a tray app.
        logger.exception("HTTP server failed to start (is port {} already in use?)", PORT)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pystray

    _setup_logging()

    # Register in Windows startup (idempotent)
    _register_startup()

    # Start HTTP server in a background daemon thread
    threading.Thread(target=_run_server, daemon=True).start()

    # System tray icon
    def on_quit(icon, _item):
        icon.stop()
        sys.exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Pallia Trans Helper  ✓ Running", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop", on_quit),
    )

    icon = pystray.Icon(
        "PalliaTransHelper",
        _make_icon_image(),
        "Pallia Trans Signing Helper",
        menu,
    )
    icon.run()   # blocks; uvicorn thread keeps serving in background
