"""Truein (the attendance system): the staff list and a day's punches.

Truein reads its key from a header named exactly ``Subscription-key``. ``urllib``
rewrites header names to ``Subscription-Key``, and Truein then answers "You are
unauthorized" (the same reply it gives when no key is sent). ``http.client``
sends the name as written, so every call goes through it.

Limits seen on the live service: one staff-list call a minute, one punches call
every 40 seconds. Callers must not ask more often than that.
"""
from __future__ import annotations

import http.client
import json
from datetime import date
from urllib.parse import urlencode

from app.config import settings

HOST = "api.truein.com"
BASE_PATH = "/apis/ext/attendance/v1.0/"
TIMEOUT_SECONDS = 20
MAX_PAGES = 20


class TrueinError(Exception):
    """Truein could not be reached, or refused the request."""


def is_configured() -> bool:
    return bool(settings.truein_subscription_key)


def _get(endpoint: str, params: dict[str, str]) -> dict:
    if not is_configured():
        raise TrueinError("The Truein key is not set.")
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)
    try:
        connection.request("GET", f"{BASE_PATH}{endpoint}?{urlencode(params)}",
                           headers={"Subscription-key": settings.truein_subscription_key,
                                    "Content-Type": "application/json"})
        body = json.loads(connection.getresponse().read().decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise TrueinError(f"Truein could not be reached: {exc}") from exc
    finally:
        connection.close()
    if not isinstance(body, dict) or str(body.get("code")) != "200":
        message = body.get("message") if isinstance(body, dict) else body
        raise TrueinError(f"Truein said: {str(message)[:200]}")
    return body


def _all_pages(endpoint: str, params: dict[str, str]) -> list[dict]:
    """Every row of a list. Today's lists fit in one page; a second page inside the rate limit raises."""
    rows: list[dict] = []
    last_uid = "0"
    for _ in range(MAX_PAGES):
        body = _get(endpoint, {**params, "lastUid": last_uid})
        page = body.get("data") or []
        rows += [row for row in page if isinstance(row, dict)]
        if str(body.get("more_rows", body.get("moreRows", "0"))) != "1" or not page:
            break
        last_uid = str(body.get("last_uid") or "")
    return rows


def fetch_staff() -> list[dict]:
    """Everyone in Truein, active and inactive (``empId``, ``name``, ``mobile``, ``status``, ...)."""
    return _all_pages("getEmployeeDtls", {})


def fetch_punches(day: date) -> list[dict]:
    """Clock-ins of one day (``empid``, ``inTime``, ``outTime``; ``outTime`` is "-" until the person clocks out)."""
    return _all_pages("inOutDtls", {"from_date": day.isoformat(), "to_date": day.isoformat()})
