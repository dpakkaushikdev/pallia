"""Move invoice PDFs and uploaded documents from database columns to file storage.

    python -m scripts.move_files_to_storage status   # counts only, changes nothing
    python -m scripts.move_files_to_storage copy     # upload + record the key; database copy kept
    python -m scripts.move_files_to_storage clear    # empty the database copy of files already in storage

``copy`` is safe to stop and run again. ``clear`` checks that every file in
storage has the same size as the database copy before it empties the column,
and cannot be undone.
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select, update

from app.database import SessionLocal, init_db
from app.models import EeeTaxiDocumentFile, EeeTaxiInvoice
from app.services import file_store
from app.services.eee_taxi_files import INVOICE_FILES, PDF

SUFFIXES = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
COMMIT_EVERY = 10


def status(db) -> None:
    for kind, (data_attr, key_attr) in INVOICE_FILES.items():
        data, key = getattr(EeeTaxiInvoice, data_attr), getattr(EeeTaxiInvoice, key_attr)
        in_db = db.scalar(select(func.count()).where(data.is_not(None)))
        in_store = db.scalar(select(func.count()).where(key.is_not(None)))
        both = db.scalar(select(func.count()).where(data.is_not(None), key.is_not(None)))
        print(f"invoice {kind:15} in database {in_db:5}   in storage {in_store:5}   in both {both:5}")
    in_db = db.scalar(select(func.count()).where(func.length(EeeTaxiDocumentFile.data) > 0))
    in_store = db.scalar(select(func.count()).where(EeeTaxiDocumentFile.storage_key.is_not(None)))
    both = db.scalar(select(func.count()).where(func.length(EeeTaxiDocumentFile.data) > 0,
                                                EeeTaxiDocumentFile.storage_key.is_not(None)))
    print(f"documents               in database {in_db:5}   in storage {in_store:5}   in both {both:5}")


def _progress(db, number: int, total: int) -> None:
    if number % COMMIT_EVERY == 0 or number == total:
        db.commit()
        db.expire_all()
        print(f"  {number}/{total}", flush=True)


def copy(db) -> None:
    file_store.ensure_bucket()
    moved = 0
    for kind, (data_attr, key_attr) in INVOICE_FILES.items():
        data_col, key_col = getattr(EeeTaxiInvoice, data_attr), getattr(EeeTaxiInvoice, key_attr)
        ids = db.scalars(select(EeeTaxiInvoice.id).where(data_col.is_not(None), key_col.is_(None))).all()
        print(f"invoice {kind}: {len(ids)} to copy", flush=True)
        for number, invoice_id in enumerate(ids, 1):
            inv = db.get(EeeTaxiInvoice, invoice_id)
            key = f"invoices/{inv.batch_id}/{inv.id}/{kind}-moved.pdf"
            file_store.put(key, bytes(getattr(inv, data_attr)), PDF)
            setattr(inv, key_attr, key)
            moved += 1
            _progress(db, number, len(ids))
    ids = db.scalars(select(EeeTaxiDocumentFile.id).where(
        func.length(EeeTaxiDocumentFile.data) > 0, EeeTaxiDocumentFile.storage_key.is_(None))).all()
    print(f"documents: {len(ids)} to copy", flush=True)
    for number, file_id in enumerate(ids, 1):
        doc = db.get(EeeTaxiDocumentFile, file_id)
        key = f"documents/{doc.entry_id}/{doc.id}{SUFFIXES.get(doc.content_type, '')}"
        file_store.put(key, bytes(doc.data), doc.content_type)
        doc.storage_key = key
        moved += 1
        _progress(db, number, len(ids))
    print(f"copied {moved} file(s); the database copies are untouched")


def clear(db) -> None:
    """Sizes are compared inside the database and by a header request, so nothing is downloaded."""
    cleared = 0
    for kind, (data_attr, key_attr) in INVOICE_FILES.items():
        data_col, key_col = getattr(EeeTaxiInvoice, data_attr), getattr(EeeTaxiInvoice, key_attr)
        rows = db.execute(select(EeeTaxiInvoice.id, key_col, func.length(data_col))
                          .where(data_col.is_not(None), key_col.is_not(None))).all()
        print(f"invoice {kind}: {len(rows)} to clear", flush=True)
        for number, (invoice_id, key, length) in enumerate(rows, 1):
            if file_store.size(key) != length:
                raise SystemExit(f"Stopped: storage copy of invoice {invoice_id} ({kind}) has a different size.")
            db.execute(update(EeeTaxiInvoice).where(EeeTaxiInvoice.id == invoice_id).values({data_attr: None}))
            cleared += 1
            _progress(db, number, len(rows))
    rows = db.execute(select(EeeTaxiDocumentFile.id, EeeTaxiDocumentFile.storage_key,
                             func.length(EeeTaxiDocumentFile.data))
                      .where(func.length(EeeTaxiDocumentFile.data) > 0,
                             EeeTaxiDocumentFile.storage_key.is_not(None))).all()
    print(f"documents: {len(rows)} to clear", flush=True)
    for number, (file_id, key, length) in enumerate(rows, 1):
        if file_store.size(key) != length:
            raise SystemExit(f"Stopped: storage copy of document {file_id} has a different size.")
        db.execute(update(EeeTaxiDocumentFile).where(EeeTaxiDocumentFile.id == file_id).values(data=b""))
        cleared += 1
        _progress(db, number, len(rows))
    print(f"cleared {cleared} database copies")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in ("status", "copy", "clear"):
        raise SystemExit(__doc__)
    if action != "status" and not file_store.enabled():
        raise SystemExit("File storage is not configured (SUPABASE_URL, SUPABASE_KEY, FILE_STORAGE_BUCKET).")
    init_db()
    with SessionLocal() as db:
        {"status": status, "copy": copy, "clear": clear}[action](db)
        status(db) if action != "status" else None


if __name__ == "__main__":
    main()
