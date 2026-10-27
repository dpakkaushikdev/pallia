"""Snapshot supporting files with an invoice before marking their source used."""
import io
from datetime import datetime

from sqlalchemy import select, func
from app.models import EeeTaxiDocumentEntry, EeeTaxiDocumentAudit, EeeTaxiDocumentFile


def normalize_route(value):
    return ' '.join((value or '').split()).upper()


CATEGORY_ORDER = {"ds": 0, "parking": 1, "toll_mcd": 2, "gps": 3, "email_screenshot": 4}
DOCUMENT_LABELS = {"ds": "DS", "parking": "Parking", "toll_mcd": "Toll / MCD", "gps": "GPS",
                   "email_screenshot": "Email Screenshot"}


def available_documents(db, route_nos, client_profile):
    """Route -> {category: file count} for saved files, without loading file contents."""
    routes = {normalize_route(route) for route in route_nos} - {''}
    if not routes:
        return {}
    rows = db.execute(select(EeeTaxiDocumentEntry.route_no, EeeTaxiDocumentFile.category,
                             func.count(EeeTaxiDocumentFile.id))
        .join(EeeTaxiDocumentFile, EeeTaxiDocumentFile.entry_id == EeeTaxiDocumentEntry.id)
        .where(EeeTaxiDocumentEntry.is_saved.is_(True), EeeTaxiDocumentFile.category != 'invoice',
               EeeTaxiDocumentEntry.client_profile == client_profile,
               EeeTaxiDocumentEntry.route_no.in_(routes))
        .group_by(EeeTaxiDocumentEntry.route_no, EeeTaxiDocumentFile.category)).all()
    found = {}
    for route, category, count in rows:
        found.setdefault(route, {})[category] = count
    return found


def missing_documents(row, client_profile, present):
    """Labels of expected documents not attached; informational only, never blocks generation.

    DS and GPS: EY every trip, PWC rentals only. Email Screenshot: EY only.
    Parking / Toll / MCD: whenever that CSV column is non-zero.
    """
    required = {"ds", "gps"} if client_profile == "ey" or row.booking_type == "rental" else set()
    if client_profile == "ey":
        required.add("email_screenshot")
    if row.parking_amount:
        required.add("parking")
    if row.toll_amount or row.mcd_amount:
        required.add("toll_mcd")
    return tuple(DOCUMENT_LABELS[category] for category in CATEGORY_ORDER
                 if category in required and category not in present)


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
    return sorted(files, key=lambda item: (CATEGORY_ORDER.get(item.category, 99), item.created_at, item.id))


def _stamp_label(page, label):
    """Print the document name in a boxed label at the page's top-left corner."""
    from pypdf import PdfReader, Transformation
    from reportlab.pdfgen import canvas

    page.transfer_rotation_to_content()
    box = page.cropbox
    width, height = float(box.width), float(box.height)
    font, size, pad, margin = "Helvetica-Bold", 14, 6, 18
    output = io.BytesIO()
    overlay = canvas.Canvas(output, pagesize=(width, height))
    x, y = margin, height - margin - size - 2 * pad
    overlay.setFillColorRGB(1, 1, 1)
    overlay.rect(x, y, overlay.stringWidth(label, font, size) + 2 * pad, size + 2 * pad, fill=1, stroke=1)
    overlay.setFillColorRGB(0, 0, 0)
    overlay.setFont(font, size)
    overlay.drawString(x + pad, y + pad + 2, label)
    overlay.showPage()
    overlay.save()
    page.merge_transformed_page(PdfReader(io.BytesIO(output.getvalue())).pages[0],
                                Transformation().translate(float(box.left), float(box.bottom)))


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
        first_page = len(writer.pages)
        if document.content_type == "application/pdf":
            supporting = PdfReader(io.BytesIO(document.data))
            if not supporting.pages:
                continue
            writer.append(supporting)
        else:
            image_pdf = _image_pdf(document.data, page_size)
            writer.append(PdfReader(io.BytesIO(image_pdf)))
        label = DOCUMENT_LABELS.get(document.category)
        if label:
            for page in writer.pages[first_page:]:
                _stamp_label(page, label)
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
