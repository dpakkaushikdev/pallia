"""One-time copy of the local SQLite database into Supabase Postgres.

Usage (from the project root, with SUPABASE_DB_URL set in .env):

    .venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_supabase.py

Safe to re-run: tables that already have rows in Supabase are skipped, so
nothing is duplicated or overwritten. `api_usage_events` is skipped because
Supabase already holds those rows from the analytics forwarder.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import Integer, create_engine, func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.config import settings  # noqa: E402
from app.database import Base, engine as target_engine, init_db  # noqa: E402

SQLITE_URL = "sqlite:///./data/app.db"
SKIP_TABLES = {"api_usage_events"}


def _reset_sequence(conn, table) -> None:
    """Move a serial id's sequence past the copied ids so new inserts don't collide."""
    pk = list(table.primary_key.columns)
    if len(pk) != 1 or not isinstance(pk[0].type, Integer):
        return
    col = pk[0].name
    conn.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col}'), "
        f"COALESCE((SELECT MAX({col}) FROM {table.name}), 1))"
    ))


def migrate() -> None:
    if not settings.uses_supabase_db:
        sys.exit("SUPABASE_DB_URL is not set in .env; nothing to migrate into.")

    source_engine = create_engine(SQLITE_URL)
    init_db()  # creates any missing tables in Supabase

    with source_engine.connect() as src, target_engine.begin() as dst:
        for table in Base.metadata.sorted_tables:  # parents before children
            if table.name in SKIP_TABLES:
                print(f"skip   {table.name} (already in Supabase)")
                continue
            existing = dst.execute(select(func.count()).select_from(table)).scalar()
            if existing:
                print(f"skip   {table.name} ({existing} rows already in Supabase)")
                continue
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            if rows:
                dst.execute(table.insert(), rows)
                _reset_sequence(dst, table)
            print(f"copied {table.name}: {len(rows)} rows")


if __name__ == "__main__":
    migrate()
