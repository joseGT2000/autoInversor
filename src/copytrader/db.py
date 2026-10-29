"""Conexión a Postgres (Supabase). El esquema está en db/schema.sql."""
import json
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


def sync_settings(database_url: str, config: dict) -> None:
    """Publica config.yaml en la tabla settings para que la Edge Function use los mismos parámetros."""
    with connect(database_url) as conn:
        conn.execute(
            """insert into settings (id, config) values (1, %s)
               on conflict (id) do update set config = excluded.config, updated_at = now()""",
            (json.dumps(config),))
