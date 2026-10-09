"""Snapshot supporting files with an invoice before marking their source used."""
import io
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
        .where(EeeTaxiDocumentEntry.is_saved.is_(True), EeeTaxiDocumentFile.category != 'invoice',
               EeeTaxiDocumentEntry.client_profile == client_profile,
               EeeTaxiDocumentEntry.route_no.in_(routes))
        .group_by(EeeTaxiDocumentEntry.route_no)).all()
    return dict(rows)


def _image_pdf(image_data, page_size):
    """Turn a screenshot into one full-size invoice-paper PDF page."""
    from PIL import Image, ImageOps
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    with Image.open(io.BytesIO(image_data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        white = Image.new("RGBA", image.size, "white")
        white.alpha_composite(image)
        flattened = io.BytesIO()
        white.convert("RGB").save(flattened, format="JPEG", quality=92)
    flattened.seek(0)
    output = io.BytesIO()
    page = canvas.Canvas(output, pagesize=page_size, pageCompression=1)
    page.drawImage(ImageReader(flattened), 0, 0, width=page_size[0], height=page_size[1],
                   preserveAspectRatio=True, anchor="c")
    page.showPage()
    page.save()
    return output.getvalue()


def ordered_documents(files):
    """Sort supporting attachments into invoice packet order."""
    category_order = {"ds": 0, "parking": 1, "toll_mcd": 2, "gps": 3, "email_screenshot": 4}
    return sorted(files, key=lambda item: (category_order.get(item.category, 99), item.created_at, item.id))


def append_supporting_pages(invoice_pdf, files):
    """Place the invoice first, followed by DS, parking, toll, GPS and EY email pages."""
    from pypdf import PdfReader, PdfWriter

    files = ordered_documents(files)
    original = PdfReader(io.BytesIO(invoice_pdf))
    if not original.pages:
        raise ValueError("The generated invoice PDF has no pages.")
    page_size = (float(original.pages[0].mediabox.width), float(original.pages[0].mediabox.height))
    writer = PdfWriter()
    writer.append(original)
    for document in files:
        if document.content_type == "application/pdf":
            supporting = PdfReader(io.BytesIO(document.data))
            if not supporting.pages:
                continue
            writer.append(supporting)
        else:
            image_pdf = _image_pdf(document.data, page_size)
            writer.append(PdfReader(io.BytesIO(image_pdf)))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def attach_documents(invoice, batch, db):
    if not invoice.pdf_data:
        return
    route = normalize_route(invoice.route_no)
    if not route:
        return
    entry = db.scalar(select(EeeTaxiDocumentEntry).where(
        EeeTaxiDocumentEntry.client_profile == (batch.client_profile or 'pwc'),
        EeeTaxiDocumentEntry.route_no == route,
        EeeTaxiDocumentEntry.is_saved.is_(True),
    ).with_for_update())
    files = [file for file in entry.files if file.category != 'invoice'] if entry else []
    if not entry or not files:
        return
    invoice.pdf_data = append_supporting_pages(invoice.pdf_data, files)
    entry.used_revision = entry.revision
    entry.used_at = datetime.utcnow()
    entry.used_invoice_no = invoice.invoice_no
    db.add(EeeTaxiDocumentAudit(entry_id=entry.id, client_profile=entry.client_profile,
        route_no=entry.route_no, action='used', actor=batch.created_by or 'invoice generation',
        details={'invoice_id': invoice.id, 'invoice_no': invoice.invoice_no, 'revision': entry.revision}))
