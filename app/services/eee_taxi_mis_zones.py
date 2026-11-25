"""Clients and zones of the EEE-Taxi MIS, with the spellings the team's sheets used.

The lists come from the Oct/Nov 2026 "Corp Trips Mis" workbook, where one client
or zone was typed several ways ("Rastey", "Rastey Cabs", "Ghziabad", "Aiport").
Every spelling is folded onto one name so filters and totals stay whole.
"""
from __future__ import annotations

import re

COMPANIES = (
    "MMT", "Amazon", "ARC ETS", "ARC Adhoc", "ATOZ", "B2C", "Capgemini", "Dalmia", "Decathlon",
    "Coforge", "Eco Car Rental", "EY", "Google", "Maintenance", "MongoDB", "PWC", "Rastey Cab", "SAP India",
    "Seahawk Gartner", "Supreme Gartner", "Swift", "TATA Play", "Wipro",
)
# A one-off client: the trip is filed under Others and its name is typed in ``company_other``.
OTHERS = "Others"
_added: tuple[str, ...] = ()   # clients added on the MIS page (table eee_taxi_mis_clients)
COMPANY_ALIASES = {
    "arcetc": "ARC ETS", "arc": "ARC ETS", "arcadhoc": "ARC Adhoc", "mongo": "MongoDB",
    "decathalon": "Decathlon", "ecocar": "Eco Car Rental", "rastey": "Rastey Cab", "rasteycabs": "Rastey Cab",
    "rastecabs": "Rastey Cab", "rastecab": "Rastey Cab", "sap": "SAP India", "supmeregartner": "Supreme Gartner", "swiftroad": "Swift", "tata": "TATA Play", "pricewaterhousecoopers": "PWC",
    "makemytrip": "MMT", "supermegartner": "Supreme Gartner", "dalmiabharatltd": "Dalmia", "eco": "Eco Car Rental",
    "ecorentel": "Eco Car Rental", "ecorental": "Eco Car Rental", "maintancance": "Maintenance",
    "maintenence": "Maintenance", "manitenance": "Maintenance", "tatapaly": "TATA Play", "swiftroadlink": "Swift",
    "other": OTHERS,
}
ZONES = ("Airport", "Delhi", "Gurgaon", "Gr Gurgaon", "Noida", "Greater Noida", "Ghaziabad", "Faridabad", "Bahadurgarh")
ZONE_ALIASES = {
    "aiport": "Airport", "ariport": "Airport", "gurugram": "Gurgaon", "gurgon": "Gurgaon", "ggn": "Gurgaon",
    "grgurgaon": "Gr Gurgaon", "greatergurgaon": "Gr Gurgaon", "grnoida": "Greater Noida",
    "gaziabad": "Ghaziabad", "ghziabad": "Ghaziabad", "ghazibad": "Ghaziabad", "gzb": "Ghaziabad",
    "bahdurgarh": "Bahadurgarh", "newdelhi": "Delhi",
}
AIRPORT_WORDS = re.compile(r"\bairport\b|\bterminal\s*-?\s*[123]\b|\bt[123]\b|\bigi\b|indira gandhi int", re.I)
# Longer names first, so "Greater Noida" is not read as "Noida".
ZONE_WORDS = (
    ("greater noida", "Greater Noida"), ("gurgaon", "Gurgaon"), ("gurugram", "Gurgaon"), ("noida", "Noida"),
    ("ghaziabad", "Ghaziabad"), ("gaziabad", "Ghaziabad"), ("faridabad", "Faridabad"),
    ("bahadurgarh", "Bahadurgarh"), ("delhi", "Delhi"),
)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def set_added_companies(names: list[str]) -> None:
    """Called at the start of every MIS request with the clients saved in the database."""
    global _added
    _added = tuple(name for name in names if name not in COMPANIES and name != OTHERS)


def all_companies() -> tuple[str, ...]:
    """Built-in clients, then the ones added on the MIS page, then Others."""
    return COMPANIES + _added + (OTHERS,)


def canonical_company(value: str) -> str | None:
    """Client-list spelling of a typed company name, or None when it is not a client."""
    key = _key(value)
    return next((name for name in all_companies() if _key(name) == key), COMPANY_ALIASES.get(key))


def new_company_name(value: str) -> str:
    """A name fit to join the client list; raises ValueError when it is empty, too long or already there."""
    name = " ".join((value or "").split())
    if not _key(name) or len(name) > 32:
        raise ValueError("Client name must have letters or digits and at most 32 characters.")
    existing = canonical_company(name)
    if existing:
        raise ValueError(f"'{existing}' is already in the client list.")
    return name


def canonical_zone(value: str) -> str:
    """Known zones in one spelling; anything else (e.g. 'Disposal') is kept as typed."""
    key = _key(value)
    return next((zone for zone in ZONES if _key(zone) == key), ZONE_ALIASES.get(key, value.strip()))


def _pincode_zone(pincode: str) -> str:
    for prefix, zone in (("110", "Delhi"), ("122", "Gurgaon"), ("121", "Faridabad"), ("2013", "Noida"),
                         ("201", "Ghaziabad"), ("124", "Bahadurgarh")):
        if pincode.startswith(prefix):
            return zone
    return ""


def guess_zone(address: str) -> str:
    """Zone named by an address; blank when it gives no clear answer."""
    address = address or ""
    if AIRPORT_WORDS.search(address):
        return "Airport"
    pincode = re.search(r"\b\d{6}\b", address)
    if pincode and _pincode_zone(pincode[0]):
        return _pincode_zone(pincode[0])
    text = address.lower()
    found = set()
    for word, zone in ZONE_WORDS:
        if word in text:
            found.add(zone)
            text = text.replace(word, " ")
    # NCR addresses often mention Delhi as well ("Indirapuram, Ghaziabad, Delhi NCR").
    if len(found) > 1:
        found.discard("Delhi")
    return found.pop() if len(found) == 1 else ""
