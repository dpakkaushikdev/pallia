"""EY calculations, kept independent of PWC's fixed fares and hour rounding."""
import csv
import io
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from app.services.ey_clients import ey_is_local, ey_tax
from app.services.ey_csv import clock_minutes, duration_minutes
from app.services.ey_rates import EyRateCard

ZERO = Decimal("0")


def _plain_amount(value: Decimal) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class EyFare:
    base: Decimal
    charged_minutes: int
    time_charge: Decimal
    charged_kms: Decimal
    km_charge: Decimal
    night: Decimal
    fare: Decimal
    package_label: str = ""
    extra_time_minutes: int = 0


def calculate_ey_fare(row, rates: EyRateCard) -> EyFare:
    minutes = duration_minutes(row.trip_duration_str)
    start = clock_minutes(row.pickup_time_str)
    end = start + minutes
    night = ZERO
    for day in range(-1, end // 1440 + 1):
        night_start = day * 1440 + rates.night_start_hour * 60 + rates.night_start_minute
        night_end = day * 1440 + rates.night_end_hour * 60 + rates.night_end_minute
        if night_end < night_start:
            night_end += 1440
        if max(start, night_start) < min(end, night_end):
            night = rates.night_charge
            break
    if row.booking_type == "p2p":
        charged = max(0, minutes - rates.p2p_grace_minutes)
        time_charge = charged * rates.p2p_per_minute
        km_charge = row.total_kms * rates.p2p_per_km
        fare = (max(rates.p2p_minimum, time_charge + km_charge) + night).quantize(Decimal("0.01"))
        return EyFare(ZERO, charged, time_charge, row.total_kms, km_charge, night, fare)
    package = next((p for p in rates.packages if p.fare == row.package), None)
    if package is None:
        raise ValueError(f"Unknown EY rental package: {row.package}")
    # EY upgrades are strictly after the thresholds; exactly 5h/7h/70km
    # stays in the starting/current package and incurs its extra charges.
    if package != rates.packages[-1] and (
        minutes > rates.large_upgrade_minutes or row.billing_kms > rates.large_upgrade_kms
    ):
        package = rates.packages[-1]
    elif package == rates.packages[0] and minutes > rates.medium_upgrade_minutes:
        package = rates.packages[1]
    extra_minutes = max(0, minutes - package.hours * 60 - rates.rental_grace_minutes)
    extra_time_minutes = max(0, minutes - package.hours * 60)
    # Retain the existing CSV kilometre treatment (whole billing kilometres).
    extra_kms = Decimal(max(0, int(row.billing_kms) - package.kms))
    time_charge = extra_minutes * rates.extra_minute_rate
    km_charge = extra_kms * rates.extra_km_rate
    fare = (Decimal(package.fare) + time_charge + km_charge + night).quantize(Decimal("0.01"))
    return EyFare(Decimal(package.fare), extra_minutes, time_charge, extra_kms, km_charge, night, fare,
                  f"{package.hours}/{package.kms}", extra_time_minutes)


def fare_description(row, rates):
    result = calculate_ey_fare(row, rates)
    if row.booking_type == "p2p":
        trip_minutes = duration_minutes(row.trip_duration_str)
        lines = ["POINT TO POINT",
                 f"Total Hrs {trip_minutes} Mins ({result.charged_minutes}*{rates.p2p_per_minute} = {_plain_amount(result.time_charge)})",
                 f"Total Kms {_plain_amount(row.total_kms)} Km ({_plain_amount(row.total_kms)}*{rates.p2p_per_km:.2f} = {_plain_amount(result.km_charge)})",
                 f"Night Charge = {_plain_amount(result.night)}"]
    else:
        raw_extra = result.extra_time_minutes
        extra_clock = f"{raw_extra // 60}:{raw_extra % 60:02d}"
        rate = _plain_amount(rates.extra_minute_rate)
        if raw_extra > rates.rental_grace_minutes:
            calculation = f"{raw_extra}-{rates.rental_grace_minutes}={result.charged_minutes}*{rate}"
        else:
            calculation = f"max(0,{raw_extra}-{rates.rental_grace_minutes})={result.charged_minutes}*{rate}"
        lines = [f"Rental ({result.package_label} = {result.base:.2f})",
                 f"Extra Hrs {extra_clock} ({calculation})={_plain_amount(result.time_charge)}",
                 f"Extra Kms {result.charged_kms} x {rates.extra_km_rate} = {result.km_charge:.2f}",
                 f"Night Charge = {_plain_amount(result.night)}"]
    if row.trip_fare != result.fare:
        lines.append(f"Reviewed fare adjustment: {row.trip_fare - result.fare:+.2f}")
    lines.append(f"Total Fare without Tax = {_plain_amount(row.trip_fare)}")
    return tuple(lines)


def apply_ey_fares(rows, rates, overrides=None):
    overrides = overrides or {}
    result = []
    for row in rows:
        fare = overrides.get(row.row_index, calculate_ey_fare(row, rates).fare)
        minimum = rates.p2p_minimum + calculate_ey_fare(row, rates).night if row.booking_type == "p2p" else ZERO
        if row.booking_type == "p2p" and fare < minimum:
            raise ValueError(f"EY row {row.row_index + 2}: fare cannot be below the P2P minimum plus night charge {minimum}.")
        result.append(replace(row, trip_fare=fare, tax_base=fare, total_amount=ZERO))
    return result


def generate_ey_calc_csv(rows, headers, rates):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(headers + ["Row", "EY Route", "Booking Type", "Effective Package", "Billable Minutes", "Time Charge", "Billed Kms",
                               "Km Charge", "Night Charge", "Calc_Trip_Fare", "Toll / Parking / MCD", "GST", "Calc Total"])
    for row in rows:
        fare = calculate_ey_fare(row, rates)
        tax = sum(ey_tax(fare.fare + row.parking, ey_is_local(row.client_gstin)))
        writer.writerow(list(row.raw_values) + [row.row_index, row.route_no, row.booking_type, fare.package_label,
            fare.charged_minutes, fare.time_charge, fare.charged_kms, fare.km_charge, fare.night,
            fare.fare, row.parking, tax, fare.fare + row.parking + tax])
    return buf.getvalue().encode("utf-8-sig")


def parse_ey_overrides(content, rows):
    """EY Calc_Trip_Fare is fare only; toll is never counted twice on re-upload."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not {"Row", "EY Route", "Calc_Trip_Fare"}.issubset(reader.fieldnames or []):
        raise ValueError("Re-upload the EY calculated CSV with Row, EY Route and Calc_Trip_Fare columns.")
    expected = {r.row_index: r for r in rows}
    overrides = {}
    for values in reader:
        try:
            idx = int(values["Row"])
            amount = Decimal(values["Calc_Trip_Fare"])
            if idx not in expected or values["EY Route"] != expected[idx].route_no:
                raise ValueError("The calculated CSV does not match the original EY trips.")
            if idx in overrides or not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("EY overrides need unique rows and positive fares with at most two decimals.")
            overrides[idx] = amount
        except (InvalidOperation, TypeError, KeyError) as exc:
            raise ValueError("Invalid EY calculated CSV fare or row.") from exc
    if set(overrides) != set(expected):
        raise ValueError("The calculated CSV must contain every original EY trip; remove trips on the review screen.")
    return overrides
