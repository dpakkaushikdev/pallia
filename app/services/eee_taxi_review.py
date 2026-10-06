"""Review step before an EEE-Taxi batch gets invoice numbers.

The user sees every trip's fare, toll, GST and total, plus anything odd about
the row, and removes the trips that are wrong. Only the trips that remain are
numbered, so removing one never leaves a gap in the DL/HO series.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import EeeTaxiInvoice
from app.services.eee_taxi_clients import UnknownClientError, is_local, lookup_client
from app.services.eee_taxi_cost_centres import get_cost_centre_state, normalize_vehicle_no
from app.services.eee_taxi_csv import EeeTaxiRow
from app.services.eee_taxi_pdf import compute_tax
from app.services.eee_taxi_profiles import row_client, row_tax

_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class ReviewRow:
    row_index: int
    route_no: str
    trip_date: str
    entity_name: str
    booking_type: str
    car_no: str
    guest_name: str
    fare: Decimal
    toll: Decimal
    gst: Decimal
    total: Decimal
    warnings: tuple[str, ...]
    eng_code: str = ""

    def to_dict(self) -> dict:
        return {
            "row_index": self.row_index,
            "route_no": self.route_no,
            "trip_date": self.trip_date,
            "entity_name": self.entity_name,
            "booking_type": self.booking_type,
            "car_no": self.car_no,
            "guest_name": self.guest_name,
            "fare": f"{self.fare:.2f}",
            "toll": f"{self.toll:.2f}",
            "gst": f"{self.gst:.2f}",
            "total": f"{self.total:.2f}",
            "warnings": list(self.warnings),
            "eng_code": self.eng_code,
        }


def _invoiced_routes(db: Session, route_nos: set[str]) -> dict[str, str]:
    """Route no -> invoice number, for trips already invoiced in an earlier batch."""
    if not route_nos:
        return {}
    found = (
        db.query(EeeTaxiInvoice.route_no, EeeTaxiInvoice.invoice_no)
        .filter(EeeTaxiInvoice.route_no.in_(route_nos), EeeTaxiInvoice.invoice_no.isnot(None))
        .all()
    )
    return {route: number for route, number in found}


def _row_warnings(row: EeeTaxiRow, total: Decimal, repeats: Counter, invoiced: dict[str, str],
                  cost_centres: set[str], client_master=None) -> list[str]:
    warnings: list[str] = []
    route = row.route_no.strip()
    if not route:
        warnings.append("No DS no/Route No.")
    elif repeats[route] > 1:
        warnings.append(f"DS no/Route No {route} appears {repeats[route]} times in this file.")
    if route in invoiced:
        warnings.append(f"Already invoiced as {invoiced[route]}.")
    try:
        row_client(row, client_master)
    except UnknownClientError:
        warnings.append(f"Unknown client GSTIN {row.client_gstin}; the invoice cannot be made.")
    if not row.car_no.strip():
        warnings.append("No car number.")
    elif normalize_vehicle_no(row.car_no) not in cost_centres:
        warnings.append(f"Car {row.car_no} has no Tally cost centre (Masters -> Cost centres).")
    if row.tax_base <= 0:
        warnings.append("Fare is zero.")
    if row.parking < 0:
        warnings.append("Toll is negative.")
    computed = row.tax_base + row.parking + sum(row_tax(row))
    if row.total_amount and abs(row.total_amount - computed) > _TOLERANCE:
        warnings.append(f"CSV total {row.total_amount:.2f} differs from fare + toll + GST {computed:.2f}.")
    if total <= 0:
        warnings.append("Total is zero.")
    return warnings


def review_rows(db: Session, rows: list[EeeTaxiRow], client_master=None) -> list[ReviewRow]:
    """The breakdown and warnings shown for each trip before numbering."""
    repeats = Counter(r.route_no.strip() for r in rows if r.route_no.strip())
    invoiced = _invoiced_routes(db, set(repeats))
    cost_centres = {c.vehicle_no for c in get_cost_centre_state(db).rows}
    out: list[ReviewRow] = []
    for row in rows:
        base = row.tax_base + row.parking
        gst = sum(row_tax(row))
        total = row.total_amount or base + gst
        out.append(ReviewRow(
            row_index=row.row_index,
            route_no=row.route_no,
            trip_date=row.trip_date.isoformat(),
            entity_name=row.entity_name,
            booking_type=row.booking_type,
            car_no=row.car_no,
            guest_name=row.guest_name,
            fare=row.tax_base,
            toll=row.parking,
            gst=total - base,
            total=total,
            warnings=tuple(_row_warnings(row, total, repeats, invoiced, cost_centres, client_master)),
            eng_code=row.eng_code,
        ))
    return out
