"""Conexión a Postgres (Supabase). El esquema está en db/schema.sql."""
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


@contextmanager
def connect(database_url: str):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        yield conn


def init_schema(database_url: str) -> None:
    with connect(database_url) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
