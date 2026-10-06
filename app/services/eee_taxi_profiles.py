"""Explicit client dispatch; calls without a profile retain PWC behaviour."""
from app.services.eee_taxi_clients import is_local, lookup_client
from app.services.eee_taxi_csv import format_invoice_no, parse_eee_taxi_csv
from app.services.eee_taxi_pdf import compute_tax
from app.services.eee_taxi_rates import DEFAULT_RATE_CARD, _from_json, get_rate_card, rate_card_to_dict
from app.services.ey_clients import ey_is_local, ey_tax, lookup_ey_client
from app.services.ey_csv import parse_ey_csv
from app.services.ey_rates import EyRateCard, get_ey_rates
from app.services.eee_taxi_client_master import get_client_master


def rates_for_client(db, profile="pwc"):
    return get_ey_rates(db) if profile == "ey" else get_rate_card(db)


def snapshot_rates(rates):
    return rates.model_dump(mode="json") if isinstance(rates, EyRateCard) else rate_card_to_dict(rates)


def restore_rates(batch):
    if batch.client_profile == "ey":
        return EyRateCard.model_validate(batch.rates_snapshot or {})
    return _from_json(batch.rates_snapshot) if batch.rates_snapshot else DEFAULT_RATE_CARD


def parse_trips(content, rates, client_master=None):
    return (parse_ey_csv(content, rates, client_master) if isinstance(rates, EyRateCard)
            else parse_eee_taxi_csv(content, rates, client_master=client_master))


def client_master_for(db, profile="pwc"):
    return get_client_master(db, profile)


def invoice_number(fy, suffix, profile="pwc"):
    return f"HR/HO/{fy}/{suffix:04d}" if profile == "ey" else format_invoice_no(fy, suffix)


def row_client(row, client_master=None):
    return (lookup_ey_client(row.client_gstin, client_master) if row.client_profile == "ey"
            else lookup_client(row.client_gstin, client_master))


def row_is_local(row):
    return ey_is_local(row.client_gstin) if row.client_profile == "ey" else is_local(row.client_gstin)


def row_tax(row):
    fn = ey_tax if row.client_profile == "ey" else compute_tax
    return fn(row.tax_base + row.parking, row_is_local(row))
