"""EY-only invoice layout, matching the supplied Haryana/5% sample."""
from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.amount_words import amount_in_words
from app.services.eee_taxi_pdf import LOGO_PATH, _FONT_RUPEE, _styles
from app.services.ey_clients import SELLER_ADDRESS, SELLER_GSTIN, ey_is_local, ey_tax, lookup_ey_client

COMPANY = "EEE-TAXI MOBILITY SOLUTIONS PRIVATE LIMITED"


def vehicle_model_from_cost_centre(vehicle_no, cost_centre):
    """Return the model suffix stored in a Tally cost-centre name, if any."""
    if not cost_centre:
        return ""
    match = re.search(r"\(([^()]*)\)\s*$", cost_centre.strip())
    return match.group(1).strip() if match else ""


def generate_ey_pdf(row, invoice_no, invoice_date, description, output_path: Path, client_master=None,
                    vehicle_model=""):
    """Description contains the actual fare calculation, shared with Tally."""
    buyer = lookup_ey_client(row.client_gstin, client_master)
    local = ey_is_local(row.client_gstin)
    cgst, sgst, igst = ey_tax(row.tax_base + row.parking, local)
    total = row.tax_base + row.parking + cgst + sgst + igst
    styles = _styles()
    def p(value, style="xs"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])
    def d(value):
        return value.strftime("%d-%b-%y")
    def table(rows, widths, grid=True):
        result = Table(rows, colWidths=widths)
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        if grid:
            commands.append(("GRID", (0, 0), (-1, -1), .4, colors.black))
        result.setStyle(TableStyle(commands))
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    width = A4[0] - 20 * mm
    left, right = width * .52, width * .48
    company_text = p(f"{COMPANY}\n{SELLER_ADDRESS}\nGSTIN/UIN: {SELLER_GSTIN}\nState Name: Haryana, Code: 06\nE-Mail: accounts@eeetaxi.com")
    company = company_text
    if LOGO_PATH.exists():
        company = table([[Image(str(LOGO_PATH), width=22*mm, height=18.3*mm), company_text]], [25*mm, left-25*mm-8], False)
    buyer_text = p(f"Buyer (Bill to)\n{buyer.entity_name}\n{buyer.address}\nGSTIN/UIN: {buyer.gstin}\nState Name: {buyer.state_name}, Code: {buyer.state_code}")
    metadata = [
        [p(f"Invoice No.\n{invoice_no}", "xsb"), p(f"Dated\n{d(invoice_date)}")],
        [p("Delivery Note"), p("")],
        [p(f"Reference No. & Date.\n{invoice_no} dt. {d(invoice_date)}"), p(f"Other References\nTOTAL KMS {row.total_kms} KM")],
        [p(f"Buyer's Order No.\n{row.eng_code}", "xsb"), p(f"Dated\n{d(row.trip_date)}")],
        [p(f"Dispatch Doc No.\n{row.route_no}"), p("Delivery Note Date")],
        [p(f"Dispatched through\nPICK UP TIME - {row.pickup_time_str}"), p(f"Destination\nDROP TIME - {row.drop_time_str}")],
        [p(f"Bill of Lading/LR-RR No.\ndt. {d(row.trip_date)}"),
         p(f"Motor Vehicle No.\n{row.car_no}{f'({vehicle_model})' if vehicle_model else ''}")],
    ]
    header = table([[table([[company], [buyer_text]], [left-8]), table(metadata, [(right-8)/2]*2)]], [left, right])
    label = "CAR RENTAL - LOCAL (5%)" if local else "CAR RENTAL - INTERSTATE (5%)"
    detail = "\n".join([label, f"Guest Name :- {row.guest_name}", f"From - {row.pickup_location}",
                         f"To - {row.drop_location}", *description])
    lines = [[p("Sl No."), p("Particulars", "xsb"), p("HSN/SAC"), p("GST Rate"), p("Amount", "smbr")],
             [p("1"), p(detail), p("996601"), p("5 %"), p(f"{row.tax_base:,.2f}", "smr")]]
    if row.parking:
        lines.append([p("2"), p("Toll & Parking"), p("996601"), p("5 %"), p(f"{row.parking:,.2f}", "smr")])
    taxes = [("OUTPUT CGST @2.5%", cgst), ("OUTPUT SGST @2.5%", sgst)] if local else [("OUTPUT IGST @5%", igst)]
    for label, amount in taxes:
        lines.append(["", p(label, "smb"), "", "", p(f"{amount:,.2f}", "smr")])
    lines.append(["", p("Total", "smb"), "", "", Paragraph(f'<font name="{_FONT_RUPEE}">&#8377;</font> {total:,.2f}', styles["smbr"])])
    items = table(lines, [9*mm, width-65*mm, 19*mm, 15*mm, 22*mm])
    words = amount_in_words(total).replace("Rupees ", "INR ", 1)
    signature = table([[p("for " + COMPANY)], [Spacer(1, 38)], [p("Authorised Signatory")]], [width*.4-8], False)
    footer = table([[p(f"Amount Chargeable (in words)\n{words}\n\nRemarks:\n{row.route_no}"), signature]], [width*.6, width*.4])
    story = [p("TAX INVOICE", "title"), Spacer(1, 4*mm), header, items, footer,
             Spacer(1, 2*mm), p("This is a Computer Generated Invoice", "xsc")]
    SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=10*mm, rightMargin=10*mm,
                      topMargin=10*mm, bottomMargin=10*mm).build(story)
    return output_path
