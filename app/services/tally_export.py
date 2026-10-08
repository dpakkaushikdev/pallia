"""Tally XML for EEE-Taxi invoices (TallyPrime: Gateway -> Import -> Transactions).

The layout follows a Sales voucher exported from the company's own Tally
(Accounting Invoice mode, manual numbering, DL/HO series):

    Dr  party ledger (client)                      total
    Cr  car rental ledger   [vehicle cost centre]  trip fare
    Cr  Toll & Parking      [vehicle cost centre]  toll / MCD / parking
    Cr  Output IGST @18%  -- or --  CGST 9% + SGST 9%

Tally signs amounts the other way round: the party line is negative and the
credits are positive. Only the fields Tally needs for GST, bill-wise details
and cost centres are written; Tally fills in the rest itself.

The file is UTF-16 LE with a BOM, which is what Tally writes when exporting.
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

from app.services.eee_taxi_clients import ClientRecord


@dataclass(frozen=True)
class TallyLedgers:
    """Names exactly as they appear in Tally; a mismatch makes Tally reject the voucher."""
    company: str = "A to Z UNIVERSALSOLUTIONS PVT. LTD.(DL)"
    company_gstin: str = "07AANCA3858Q1ZU"
    company_state: str = "Delhi"
    gst_registration: str = "Delhi Registration"
    voucher_type: str = "Sales"
    sales_interstate: str = "Car Rental Interstate-18%"
    sales_local: str = "CAR RENTAL LOCAL-18%"
    toll: str = "Toll & Parking"
    toll_interstate: str | None = None
    include_cost_centres: bool = True
    igst: str = "Output IGST @18%"
    cgst: str = "OUTPUT CGST @ 9%"
    sgst: str = "OUTPUT SGST @ 9%"
    hsn_interstate: str = "996601"
    hsn_local: str = "996412"
    hsn_toll: str = "996601"
    cost_category: str = "Primary Cost Category"
    payment_terms: str = "30 Days"
    gst_rate: Decimal = Decimal("18")


DEFAULT_LEDGERS = TallyLedgers()


@dataclass(frozen=True)
class TallyVoucher:
    invoice_no: str
    invoice_date: date
    trip_date: date
    route_no: str
    car_no: str
    party: ClientRecord
    is_local: bool
    fare: Decimal
    toll: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cost_centre: str
    description: tuple[str, ...] = ()
    buyer_order_no: str | None = None
    other_references: str = ""
    dispatch_doc_no: str = ""
    dispatched_through: str = ""
    destination: str = ""
    motor_vehicle_no: str = ""

    @property
    def total(self) -> Decimal:
        return self.fare + self.toll + self.cgst + self.sgst + self.igst


# ── Helpers ───────────────────────────────────────────────────────────────────

def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _amt(v: Decimal) -> str:
    return f"{v:.2f}"


def _x(text: str) -> str:
    return escape(str(text))


def _tag(name: str, value) -> str:
    return f"<{name}>{_x(value)}</{name}>"


def _pincode(address: str) -> str:
    found = re.findall(r"\b\d{6}\b", address)
    return found[-1] if found else ""


def _address_lines(address: str, width: int = 48) -> list[str]:
    """Split a one-line address into Tally address lines of roughly ``width`` chars."""
    lines: list[str] = []
    current = ""
    for part in (p.strip() for p in address.split(",") if p.strip()):
        candidate = f"{current}, {part}" if current else part
        if current and len(candidate) > width:
            lines.append(current)
            current = part
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# ── Voucher pieces ────────────────────────────────────────────────────────────

def _party_entry(v: TallyVoucher) -> str:
    total = _amt(-v.total)
    return (
        "<LEDGERENTRIES.LIST>"
        + _tag("LEDGERNAME", v.party.entity_name)
        + _tag("ISDEEMEDPOSITIVE", "Yes")
        + _tag("ISPARTYLEDGER", "Yes")
        + _tag("ISLASTDEEMEDPOSITIVE", "Yes")
        + _tag("AMOUNT", total)
        + "<BILLALLOCATIONS.LIST>"
        + _tag("NAME", v.invoice_no)
        + _tag("BILLTYPE", "New Ref")
        + _tag("AMOUNT", total)
        + "</BILLALLOCATIONS.LIST>"
        + "</LEDGERENTRIES.LIST>"
    )


def _rate_details(gst_rate: Decimal = Decimal("18")) -> str:
    # Tally lists all three heads on every taxable line; place of supply picks the tax.
    rates = (("CGST", str(gst_rate / 2)), ("SGST/UTGST", str(gst_rate / 2)), ("IGST", str(gst_rate)))
    return "".join(
        "<RATEDETAILS.LIST>"
        + _tag("GSTRATEDUTYHEAD", head)
        + _tag("GSTRATEVALUATIONTYPE", "Based on Value")
        + _tag("GSTRATE", rate)
        + "</RATEDETAILS.LIST>"
        for head, rate in rates
    )


_INVOICE_TAX_RATE = "<RATEOFINVOICETAX.LIST TYPE=\"Number\"><RATEOFINVOICETAX>18</RATEOFINVOICETAX></RATEOFINVOICETAX.LIST>"


def _invoice_tax_rate(rate: Decimal) -> str:
    return '<RATEOFINVOICETAX.LIST TYPE="Number">' + _tag("RATEOFINVOICETAX", rate) + '</RATEOFINVOICETAX.LIST>'


def _income_entry(v: TallyVoucher, ledger: str, hsn: str, amount: Decimal,
                  ledgers: TallyLedgers, description: tuple[str, ...] = (),
                  invoice_tax_rate: bool = False) -> str:
    amt = _amt(amount)
    cost_allocation = ""
    if ledgers.include_cost_centres:
        cost_allocation = (
            "<CATEGORYALLOCATIONS.LIST>"
            + _tag("CATEGORY", ledgers.cost_category)
            + _tag("ISDEEMEDPOSITIVE", "No")
            + "<COSTCENTREALLOCATIONS.LIST>"
            + _tag("NAME", v.cost_centre)
            + _tag("AMOUNT", amt)
            + "</COSTCENTREALLOCATIONS.LIST>"
            + "</CATEGORYALLOCATIONS.LIST>"
        )
    udf = ""
    if description:
        udf = (
            '<UDF:USERDESCRIPTION.LIST DESC="`User Description`" ISLIST="YES" TYPE="String" INDEX="29">'
            + "".join(f'<UDF:USERDESCRIPTION DESC="`User Description`">{_x(line)}</UDF:USERDESCRIPTION>'
                      for line in description)
            + "</UDF:USERDESCRIPTION.LIST>"
        )
    return (
        "<LEDGERENTRIES.LIST>"
        + (_invoice_tax_rate(ledgers.gst_rate) if invoice_tax_rate else "")
        + _tag("LEDGERNAME", ledger)
        + _tag("GSTOVRDNTAXABILITY", "Taxable")
        + _tag("GSTSOURCETYPE", "Ledger")
        + _tag("GSTLEDGERSOURCE", ledger)
        + _tag("HSNSOURCETYPE", "Ledger")
        + _tag("HSNLEDGERSOURCE", ledger)
        + _tag("GSTOVRDNTYPEOFSUPPLY", "Services")
        + _tag("GSTHSNNAME", hsn)
        + _tag("ISDEEMEDPOSITIVE", "No")
        + _tag("ISPARTYLEDGER", "No")
        + _tag("AMOUNT", amt)
        + _tag("VATEXPAMOUNT", amt)
        + cost_allocation
        + _rate_details(ledgers.gst_rate)
        + udf
        + "</LEDGERENTRIES.LIST>"
    )


def _tax_entry(ledger: str, amount: Decimal, gst_rate: Decimal = Decimal("18")) -> str:
    amt = _amt(amount)
    return (
        "<LEDGERENTRIES.LIST>"
        + _invoice_tax_rate(gst_rate)
        + _tag("LEDGERNAME", ledger)
        + _tag("ISDEEMEDPOSITIVE", "No")
        + _tag("ISPARTYLEDGER", "No")
        + _tag("AMOUNT", amt)
        + _tag("VATEXPAMOUNT", amt)
        + "</LEDGERENTRIES.LIST>"
    )


def _voucher(v: TallyVoucher, ledgers: TallyLedgers) -> str:
    p = v.party
    state = p.state_name
    pin = _pincode(p.address)
    address = "".join(_tag("ADDRESS", line) for line in _address_lines(p.address))
    buyer_address = "".join(_tag("BASICBUYERADDRESS", line) for line in _address_lines(p.address))
    sales_ledger = ledgers.sales_local if v.is_local else ledgers.sales_interstate
    sales_hsn = ledgers.hsn_local if v.is_local else ledgers.hsn_interstate

    entries = [_party_entry(v), _income_entry(v, sales_ledger, sales_hsn, v.fare, ledgers, v.description)]
    if v.toll:
        toll_ledger = ledgers.toll if v.is_local else (ledgers.toll_interstate or ledgers.toll)
        entries.append(_income_entry(v, toll_ledger, ledgers.hsn_toll, v.toll, ledgers, invoice_tax_rate=True))
    if v.is_local:
        entries += [_tax_entry(ledgers.cgst, v.cgst, ledgers.gst_rate), _tax_entry(ledgers.sgst, v.sgst, ledgers.gst_rate)]
    else:
        entries.append(_tax_entry(ledgers.igst, v.igst, ledgers.gst_rate))

    attrs = f'VCHTYPE={quoteattr(ledgers.voucher_type)} ACTION="Create" OBJVIEW="Invoice Voucher View"'
    return (
        '<TALLYMESSAGE xmlns:UDF="TallyUDF">'
        f"<VOUCHER {attrs}>"
        f'<ADDRESS.LIST TYPE="String">{address}</ADDRESS.LIST>'
        f'<BASICBUYERADDRESS.LIST TYPE="String">{buyer_address}</BASICBUYERADDRESS.LIST>'
        + _tag("DATE", _d(v.invoice_date))
        + _tag("REFERENCEDATE", _d(v.invoice_date))
        + _tag("EFFECTIVEDATE", _d(v.invoice_date))
        + _tag("BILLOFLADINGDATE", _d(v.trip_date))
        + _tag("GSTREGISTRATIONTYPE", "Regular")
        + _tag("VATDEALERTYPE", "Regular")
        + _tag("STATENAME", state)
        + _tag("COUNTRYOFRESIDENCE", "India")
        + _tag("PARTYGSTIN", p.gstin)
        + _tag("PLACEOFSUPPLY", state)
        + _tag("PARTYNAME", p.entity_name)
        + f'<GSTREGISTRATION TAXTYPE="GST" TAXREGISTRATION={quoteattr(ledgers.company_gstin)}>'
        + f"{_x(ledgers.gst_registration)}</GSTREGISTRATION>"
        + _tag("CMPGSTIN", ledgers.company_gstin)
        + _tag("VOUCHERTYPENAME", ledgers.voucher_type)
        + _tag("PARTYLEDGERNAME", p.entity_name)
        + _tag("VOUCHERNUMBER", v.invoice_no)
        + _tag("BASICBUYERNAME", p.entity_name)
        + _tag("CMPGSTREGISTRATIONTYPE", "Regular")
        + _tag("REFERENCE", v.invoice_no)
        + _tag("PARTYMAILINGNAME", p.entity_name)
        + _tag("PARTYPINCODE", pin)
        + _tag("CONSIGNEEGSTIN", p.gstin)
        + _tag("CONSIGNEEMAILINGNAME", p.entity_name)
        + _tag("CONSIGNEEPINCODE", pin)
        + _tag("CONSIGNEESTATENAME", state)
        + _tag("CMPGSTSTATE", ledgers.company_state)
        + _tag("CONSIGNEECOUNTRYNAME", "India")
        + _tag("BASICBASEPARTYNAME", p.entity_name)
        + _tag("NUMBERINGSTYLE", "Manual")
        + _tag("PERSISTEDVIEW", "Invoice Voucher View")
        + _tag("VCHSTATUSVOUCHERTYPE", ledgers.voucher_type)
        + _tag("VCHSTATUSTAXUNIT", ledgers.gst_registration)
        + _tag("BASICSHIPVESSELNO", v.motor_vehicle_no or v.car_no)
        + (_tag("BASICSHIPDOCUMENTNO", v.dispatch_doc_no) if v.dispatch_doc_no else "")
        + (_tag("BASICSHIPPEDBY", v.dispatched_through) if v.dispatched_through else "")
        + (_tag("BASICFINALDESTINATION", v.destination) if v.destination else "")
        + (_tag("BASICORDERREF", v.other_references) if v.other_references else "")
        + _tag("BASICDUEDATEOFPYMT", ledgers.payment_terms)
        + _tag("NARRATION", v.route_no)
        + _tag("VCHENTRYMODE", "Accounting Invoice")
        + _tag("VOUCHERTYPEORIGNAME", ledgers.voucher_type)
        + _tag("ISINVOICE", "Yes")
        + "<INVOICEORDERLIST.LIST>"
        + _tag("BASICORDERDATE", _d(v.trip_date))
        + _tag("BASICPURCHASEORDERNO", v.buyer_order_no if v.buyer_order_no is not None else v.route_no)
        + "</INVOICEORDERLIST.LIST>"
        + "".join(entries)
        + "</VOUCHER></TALLYMESSAGE>"
    )


def render_tally_xml(vouchers: list[TallyVoucher], ledgers: TallyLedgers = DEFAULT_LEDGERS) -> bytes:
    """The import file for *vouchers*, encoded the way Tally exports (UTF-16 LE + BOM)."""
    body = "".join(_voucher(v, ledgers) for v in vouchers)
    xml = (
        "<ENVELOPE>"
        "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        "<BODY><IMPORTDATA>"
        "<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME>"
        f"<STATICVARIABLES>{_tag('SVCURRENTCOMPANY', ledgers.company)}</STATICVARIABLES>"
        "</REQUESTDESC>"
        f"<REQUESTDATA>{body}</REQUESTDATA>"
        "</IMPORTDATA></BODY>"
        "</ENVELOPE>"
    )
    # No pretty-printing: whitespace between tags would become the value of empty fields.
    return codecs.BOM_UTF16_LE + xml.encode("utf-16-le")
