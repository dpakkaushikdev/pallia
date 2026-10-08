"""Haryana EY accounting settings; never use PWC's Delhi company or 18% ledgers."""
from decimal import Decimal

from app.services.ey_clients import SELLER_GSTIN
from app.services.tally_export import TallyLedgers


def ey_tally_ledgers(rates):
    if not rates.tally_company.strip():
        raise ValueError("Set the exact Haryana Tally company name and verify its ledger names in Masters > EY rate card before exporting EY invoices.")
    return TallyLedgers(
        company=rates.tally_company,
        company_gstin=SELLER_GSTIN,
        company_state="Haryana",
        voucher_type=rates.tally_voucher_type,
        gst_registration=rates.tally_registration,
        sales_local=rates.tally_sales_local,
        sales_interstate=rates.tally_sales_interstate,
        toll=rates.tally_toll,
        toll_interstate=rates.tally_toll_interstate,
        include_cost_centres=False,
        cgst=rates.tally_cgst,
        sgst=rates.tally_sgst,
        igst=rates.tally_igst,
        hsn_local="996601",
        gst_rate=Decimal("5"),
    )
