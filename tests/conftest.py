"""Point every test at a throwaway database before any app module is imported."""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="billing-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["UPLOAD_DIR"] = f"{_TMP}/uploads"
os.environ["EEE_TAXI_OUTPUT_DIR"] = f"{_TMP}/eee_taxi"
os.environ["AUTH_JWT_SECRET"] = "test-secret"
# Never let tests touch the real Supabase project in .env: SUPABASE_DB_URL would
# otherwise override DATABASE_URL and point the suite at the live database.
os.environ["SUPABASE_DB_URL"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""
