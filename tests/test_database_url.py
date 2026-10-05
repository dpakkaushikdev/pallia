"""SUPABASE_DB_URL, when set, replaces DATABASE_URL as the app's database."""
from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_falls_back_to_database_url_when_supabase_db_url_empty():
    s = _settings(database_url="sqlite:///./data/app.db", supabase_db_url="")

    assert s.sqlalchemy_database_url == "sqlite:///./data/app.db"
    assert s.uses_supabase_db is False


def test_supabase_db_url_wins_and_uses_psycopg_driver():
    s = _settings(
        database_url="sqlite:///./data/app.db",
        supabase_db_url="postgresql://postgres.abc:pw@host:6543/postgres",
    )

    assert s.sqlalchemy_database_url == "postgresql+psycopg://postgres.abc:pw@host:6543/postgres"
    assert s.uses_supabase_db is True


def test_postgres_scheme_alias_is_normalised():
    s = _settings(supabase_db_url="postgres://u:pw@host:5432/postgres")

    assert s.sqlalchemy_database_url == "postgresql+psycopg://u:pw@host:5432/postgres"


def test_explicit_driver_is_left_alone():
    s = _settings(supabase_db_url="postgresql+psycopg://u:pw@host:5432/postgres")

    assert s.sqlalchemy_database_url == "postgresql+psycopg://u:pw@host:5432/postgres"
