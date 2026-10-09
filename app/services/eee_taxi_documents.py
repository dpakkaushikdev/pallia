"""Snapshot supporting files with an invoice before marking their source used."""
import io
import re
import zipfile
from datetime import datetime

from sqlalchemy import select, func
from app.models import EeeTaxiDocumentEntry, EeeTaxiDocumentAudit, EeeTaxiDocumentFile


def normalize_route(value):
    return ' '.join((value or '').split()).upper()


def available_documents(db, route_nos, client_profile):
    """Count saved matching files in one query, without loading file contents."""
    routes = {normalize_route(route) for route in route_nos} - {''}
    if not routes:
        return {}
    rows = db.execute(select(EeeTaxiDocumentEntry.route_no, func.count(EeeTaxiDocumentFile.id))
        .join(EeeTaxiDocumentFile, EeeTaxiDocumentFile.entry_id == EeeTaxiDocumentEntry.id)
        .where(EeeTaxiDocumentEntry.is_saved.is_(True),
               EeeTaxiDocumentEntry.client_profile == client_profile,
               EeeTaxiDocumentEntry.route_no.in_(routes))
        .group_by(EeeTaxiDocumentEntry.route_no)).all()
    return dict(rows)


def safe_name(value):
    return re.sub(r'[^\w .()-]', '_', value).strip(' .')[:180] or 'document'


def attach_documents(invoice, batch, db):
    if invoice.document_zip_data:
        return
    route = normalize_route(invoice.route_no)
    if not route:
        return
    entry = db.scalar(select(EeeTaxiDocumentEntry).where(
        EeeTaxiDocumentEntry.client_profile == (batch.client_profile or 'pwc'),
        EeeTaxiDocumentEntry.route_no == route,
        EeeTaxiDocumentEntry.is_saved.is_(True),
    ).with_for_update())
    if not entry or not entry.files:
        return
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, file in enumerate(entry.files, 1):
            archive.writestr(f'{file.category}/{index:03d}_{safe_name(file.filename)}', file.data)
    invoice.document_zip_data = buffer.getvalue()
    entry.used_revision = entry.revision
    entry.used_at = datetime.utcnow()
    entry.used_invoice_no = invoice.invoice_no
    db.add(EeeTaxiDocumentAudit(entry_id=entry.id, client_profile=entry.client_profile,
        route_no=entry.route_no, action='used', actor=batch.created_by or 'invoice generation',
        details={'invoice_id': invoice.id, 'invoice_no': invoice.invoice_no, 'revision': entry.revision}))
