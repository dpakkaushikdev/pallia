"""Read and write the files behind EEE-Taxi invoices and uploaded documents.

Each file lives in file storage when it is configured (the row keeps the key)
and in the row's own column otherwise. Rows written before storage was turned
on still hold their bytes, so every read checks the key first, then the column.
"""
from __future__ import annotations

import io
import uuid

from loguru import logger

from app.services import file_store
from app.services.file_store import FileStoreError

# kind -> (column holding the bytes, column holding the storage key)
INVOICE_FILES = {
    "pdf": ("pdf_data", "pdf_key"),
    "signed_pdf": ("signed_pdf_data", "signed_pdf_key"),
    "signed_invoice": ("signed_invoice_data", "signed_invoice_key"),
}
PDF = "application/pdf"
# A printed A4 page at about 170 dpi; phone photos and scans are far larger.
MAX_IMAGE_SIDE = 2000
JPEG_QUALITY = 80


def _read(key: str | None, load_column) -> bytes | None:
    """*load_column* is called only when needed, so an unread column stays unread."""
    if key:
        try:
            return file_store.get(key)
        except FileStoreError:
            fallback = load_column()
            if not fallback:
                raise
            logger.warning("File storage read failed for {}; using the database copy.", key)
            return bytes(fallback)
    value = load_column()
    return bytes(value) if value else None


def invoice_file(inv, kind: str) -> bytes | None:
    data_attr, key_attr = INVOICE_FILES[kind]
    return _read(getattr(inv, key_attr, None), lambda: getattr(inv, data_attr))


def set_invoice_file(inv, kind: str, data: bytes | None) -> list[str]:
    """Save (or clear, with None) one invoice file.

    Returns storage keys that are no longer referenced; remove them with
    ``file_store.delete`` once the database change has been committed.
    """
    data_attr, key_attr = INVOICE_FILES[kind]
    old_key = getattr(inv, key_attr, None)
    stale = [old_key] if old_key else []
    new_key = None
    if data is not None and file_store.enabled():
        candidate = f"invoices/{inv.batch_id}/{inv.id}/{kind}-{uuid.uuid4().hex[:8]}.pdf"
        try:
            file_store.put(candidate, data, PDF)
            new_key = candidate
        except FileStoreError as exc:
            # Billing must not stop because storage is unreachable.
            logger.error("{} Keeping this PDF in the database instead.", exc)
    setattr(inv, key_attr, new_key)
    setattr(inv, data_attr, None if new_key else data)
    return stale


def invoice_file_keys(inv) -> list[str]:
    return [key for _, key_attr in INVOICE_FILES.values() if (key := getattr(inv, key_attr, None))]


def document_bytes(document) -> bytes:
    return _read(getattr(document, "storage_key", None), lambda: document.data) or b""


def store_document(entry_id: str, data: bytes, content_type: str, suffix: str) -> tuple[bytes, str | None]:
    """Returns (bytes for the row's column, storage key)."""
    if file_store.enabled():
        key = f"documents/{entry_id}/{uuid.uuid4().hex}{suffix}"
        try:
            file_store.put(key, data, content_type)
            return b"", key
        except FileStoreError as exc:
            logger.error("{} Keeping this document in the database instead.", exc)
    return data, None


def document_keys(files) -> list[str]:
    return [key for file in files if (key := getattr(file, "storage_key", None))]


def shrink_image(data: bytes, content_type: str, suffix: str) -> tuple[bytes, str, str]:
    """Scale a scan or phone photo down to print size; keep the original if that is not smaller."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            page = Image.new("RGBA", image.size, "white")
            page.alpha_composite(image)
            output = io.BytesIO()
            page.convert("RGB").save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except (OSError, ValueError) as exc:
        logger.warning("Image kept at its original size: {}", exc)
        return data, content_type, suffix
    smaller = output.getvalue()
    if len(smaller) >= len(data):
        return data, content_type, suffix
    return smaller, "image/jpeg", ".jpg"
