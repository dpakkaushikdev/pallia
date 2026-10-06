"""EY buyer and seller details transcribed from the supplied invoice.

Do not infer unknown buyer GSTINs or reuse the PWC entity list.
"""
from decimal import Decimal

from app.services.eee_taxi_clients import ClientRecord, UnknownClientError

SELLER_GSTIN = "06AANCA3858Q1ZW"
SELLER_STATE = "Haryana"
SELLER_ADDRESS = "7th Floor, Unit Nos. 701-705, Good Earth Business Bay-I, Sector-58, Gurugram, Haryana, 122098"
EY_CLIENTS = {
    "06AAEFE1763C1ZW": ClientRecord(
        entity_name="Ernst & Young LLP",
        address="Ground Floor, Plot No.67, Sector 44, Institutional Area, Gurugram, Haryana, 122003",
        gstin="06AAEFE1763C1ZW",
    ),
}


def lookup_ey_client(gstin: str) -> ClientRecord:
    try:
        return EY_CLIENTS[gstin.strip().upper()]
    except KeyError:
        raise UnknownClientError(f"EY GSTIN {gstin!r} is not in the EY company list.") from None


def ey_is_local(gstin: str) -> bool:
    return gstin.strip().startswith("06")


def ey_tax(taxable: Decimal, local: bool) -> tuple[Decimal, Decimal, Decimal]:
    if local:
        half = (taxable * Decimal("0.025")).quantize(Decimal("0.01"))
        return half, half, Decimal("0")
    return Decimal("0"), Decimal("0"), (taxable * Decimal("0.05")).quantize(Decimal("0.01"))
