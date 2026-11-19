"""Invoice PDFs and uploaded documents kept in Supabase Storage.

The database row holds only a storage key. Files used to sit in the database
itself, which filled its disk and sent every PDF over the wire whenever a row
was read.

Storage is on when SUPABASE_URL, SUPABASE_KEY (service role) and a bucket are
configured. Without them (tests, a plain SQLite run) callers keep the bytes
in the database column as before.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import httpx
from loguru import logger

from app.config import settings

TIMEOUT_SECONDS = 30.0
# Storage now and then answers with a passing 5xx; a short retry rides over it.
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 1.0
_client: httpx.Client | None = None


class FileStoreError(RuntimeError):
    """A file could not be written to or read from storage."""


def enabled() -> bool:
    return bool(settings.file_storage_bucket and settings.supabase_url and settings.supabase_key)


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT_SECONDS)
    return _client


def _headers() -> dict[str, str]:
    return {"apikey": settings.supabase_key, "Authorization": f"Bearer {settings.supabase_key}"}


def _base() -> str:
    return f"{settings.supabase_url.rstrip('/')}/storage/v1"


def _object_url(key: str) -> str:
    return f"{_base()}/object/{settings.file_storage_bucket}/{quote(key)}"


def _send(method: str, url: str, **kwargs) -> httpx.Response:
    """One request, retried on a network error or a 5xx answer."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = _http().request(method, url, **kwargs)
            if response.status_code < 500 or attempt == ATTEMPTS:
                response.raise_for_status()
                return response
        except httpx.TransportError:
            if attempt == ATTEMPTS:
                raise
        time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise AssertionError("unreachable")


def put(key: str, data: bytes, content_type: str) -> None:
    try:
        _send("POST", _object_url(key), content=data,
              headers={**_headers(), "Content-Type": content_type, "x-upsert": "true"})
    except httpx.HTTPError as exc:
        raise FileStoreError(f"Could not save {key} to file storage: {exc}") from exc


def get(key: str) -> bytes:
    try:
        response = _send("GET", _object_url(key), headers=_headers())
    except httpx.HTTPError as exc:
        raise FileStoreError(f"Could not read {key} from file storage: {exc}") from exc
    return response.content


def size(key: str) -> int:
    """Stored size in bytes, without downloading the file."""
    try:
        response = _send("HEAD", _object_url(key), headers=_headers())
        return int(response.headers["content-length"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise FileStoreError(f"Could not check {key} in file storage: {exc}") from exc


def delete(keys: list[str]) -> None:
    """Best effort: a file left behind costs a little space, never a failed request."""
    keys = [key for key in keys if key]
    if not keys or not enabled():
        return
    try:
        response = _http().request(
            "DELETE", f"{_base()}/object/{settings.file_storage_bucket}",
            headers=_headers(), json={"prefixes": keys},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("File storage: {} file(s) not removed: {}", len(keys), exc)


def ensure_bucket() -> None:
    """Create the private bucket if it is missing. Run once by the move script."""
    response = _http().get(f"{_base()}/bucket/{settings.file_storage_bucket}", headers=_headers())
    if response.status_code == 200:
        return
    created = _http().post(
        f"{_base()}/bucket", headers=_headers(),
        json={"id": settings.file_storage_bucket, "name": settings.file_storage_bucket, "public": False},
    )
    if created.status_code >= 400:
        raise FileStoreError(f"Could not create the storage bucket: {created.status_code} {created.text[:200]}")
