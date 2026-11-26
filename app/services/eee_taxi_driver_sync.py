"""Keep the EEE-Taxi driver master a copy of Truein's staff list, and read today's attendance.

Truein is where a driver is hired and where they are marked inactive, so the
master is replaced by its list. The app runs on Vercel with no background jobs:
the copy is refreshed when someone opens the Masters or MIS page and the last
attempt is older than ``SYNC_EVERY``. Details Truein does not hold (licence
expiry, alternate mobile, availability, branch, emergency contact) are carried
over from the row the driver had before.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

from app.models import EeeTaxiDriverMaster
from app.services.eee_taxi_drivers import DriverRow, DriverRows, get_driver_rows
from app.services.eee_taxi_mis import id_key, name_key, normalize_phone
from app.services.truein import TrueinError, fetch_punches, fetch_staff, is_configured

SYNC_EVERY = timedelta(minutes=30)
ATTENDANCE_EVERY = timedelta(minutes=10)   # Truein allows one punches call every 40 seconds, shared with AppSheet
IST_OFFSET = timedelta(minutes=330)
EDITOR = "Truein sync"
KEPT_FIELDS = ("alternate_mobile", "availability", "branch", "dl_expiry", "emergency_contact_name",
               "emergency_contact_number", "emergency_contact_relationship")


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _date(value: object) -> str:
    """Truein's '2024-02-05' as the master's '05/02/2024'."""
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _clock(value: object) -> str:
    """'2026-11-26 09:12:33' as '09:12'; '-' (not clocked out) as ''."""
    text = str(value or "")
    return text[11:16] if len(text) >= 16 and text[13] == ":" else ""


def _id_number(truein_id: str) -> str:
    """The running number at the end of a Truein ID ('EEED/VK/NIPS/0506' -> '506')."""
    return (re.findall(r"\d+", truein_id or "") or [""])[-1].lstrip("0")


def rows_from_truein(staff: list[dict], old_rows: list[DriverRow]) -> list[DriverRow]:
    """One master row per person in Truein, with the web app's own details kept from their old row."""
    by_id = {id_key(row.truein_id): row for row in old_rows if id_key(row.truein_id)}
    # A driver whose ID was mistyped in the old master is still the same mobile and name.
    by_person = {(normalize_phone(row.mobile), name_key(row.name)): row for row in old_rows if normalize_phone(row.mobile)}
    # ... or, when the name was spelled differently too, the same mobile and the same number in the ID.
    by_number = {(normalize_phone(row.mobile), _id_number(row.truein_id)): row for row in old_rows
                 if normalize_phone(row.mobile) and _id_number(row.truein_id)}
    rows, seen, used_ids = [], set(), set()
    for person in staff:
        truein_id, name = _text(person.get("empId"), 40), _text(person.get("name"), 120)
        key = id_key(truein_id)
        if not key or not name or key in seen:
            continue
        seen.add(key)
        mobile = normalize_phone(str(person.get("mobile") or ""))[:20]
        old = by_id.get(key) or by_person.get((mobile, name_key(name))) or by_number.get((mobile, _id_number(truein_id)))
        driver_id = old.driver_id if old and old.driver_id not in used_ids else hashlib.sha1(key.encode()).hexdigest()[:8]
        used_ids.add(driver_id)
        rows.append(DriverRow(
            driver_id=driver_id, name=name, truein_id=truein_id, mobile=mobile,
            father_name=_text(person.get("father_name"), 120), email=_text(person.get("email"), 254),
            status="Active" if str(person.get("status") or "").strip().lower() == "active" else "Deactive",
            department=_text(person.get("department"), 80), date_of_joining=_date(person.get("joining_date")),
            **({field: getattr(old, field) for field in KEPT_FIELDS} if old else {}),
        ))
    return rows


def _stored(db: Session) -> EeeTaxiDriverMaster:
    stored = db.get(EeeTaxiDriverMaster, 1)
    if stored is None:
        stored = EeeTaxiDriverMaster(id=1, rows=[])
        db.add(stored)
    return stored


def sync_drivers(db: Session) -> dict:
    """Replace the master with Truein's list. A failed or empty reply leaves the master as it was."""
    stored = _stored(db)
    # The attempt is recorded first, so a second request arriving now does not also ask Truein.
    stored.synced_at = datetime.utcnow()
    db.commit()
    old = get_driver_rows(db)
    try:
        new = DriverRows(rows=rows_from_truein(fetch_staff(), old)).rows
        if not new:
            raise TrueinError("Truein returned no staff.")
        if len(new) * 2 < len(old):
            raise TrueinError(f"Truein returned only {len(new)} staff against {len(old)} in the master; not replaced.")
    except (TrueinError, ValueError) as exc:
        logger.warning("Truein driver sync failed: {}", exc)
        stored.sync_error = str(exc)[:255]
        db.commit()
        return {"ok": False, "error": stored.sync_error}
    before = {row.driver_id: row for row in old}
    kept = [row for row in new if row.driver_id in before]
    result = {"ok": True, "drivers": len(new), "added": len(new) - len(kept), "removed": len(old) - len(kept),
              "changed": sum(1 for row in kept if row != before[row.driver_id])}
    if result["added"] or result["removed"] or result["changed"]:
        stored.rows = [row.model_dump() for row in new]
        stored.updated_by = EDITOR
        stored.updated_at = datetime.utcnow()
    stored.sync_error = None
    db.commit()
    return result


def sync_if_stale(db: Session) -> None:
    """Refresh from Truein when the last attempt is older than ``SYNC_EVERY``; never fails the page."""
    if not is_configured():
        return
    stored = db.get(EeeTaxiDriverMaster, 1)
    if stored is not None and stored.synced_at and datetime.utcnow() - stored.synced_at < SYNC_EVERY:
        return
    sync_drivers(db)


def attendance_today(db: Session) -> dict[str, dict[str, str]]:
    """Who has clocked in today: ``{Truein ID without punctuation: {"in": "09:12", "out": ""}}``.

    Read from Truein at most every ``ATTENDANCE_EVERY`` and kept on the master
    row in between; when Truein refuses, the last copy of today is served.
    """
    stored = db.get(EeeTaxiDriverMaster, 1)
    if stored is None:
        return {}
    now = datetime.utcnow()
    today = now + IST_OFFSET
    saved = stored.attendance or {}
    fresh = stored.attendance_at and now - stored.attendance_at < ATTENDANCE_EVERY
    if is_configured() and not (fresh and saved.get("day") == today.date().isoformat()):
        stored.attendance_at = now
        db.commit()
        try:
            punches = sorted(fetch_punches(today.date()), key=lambda punch: str(punch.get("inTime") or ""))
            # A person who clocks in twice keeps the later punch.
            staff = {id_key(str(punch.get("empid") or "")): {"in": _clock(punch.get("inTime")), "out": _clock(punch.get("outTime"))}
                     for punch in punches if id_key(str(punch.get("empid") or ""))}
            saved = {"day": today.date().isoformat(), "staff": staff}
            stored.attendance = saved
            db.commit()
        except TrueinError as exc:
            logger.warning("Truein attendance not refreshed: {}", exc)
    return saved.get("staff", {}) if saved.get("day") == today.date().isoformat() else {}
