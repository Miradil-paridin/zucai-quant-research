from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
load_dotenv(BACKEND_ROOT / ".env")


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    return value


def connect(*, autocommit: bool = False) -> psycopg.Connection:
    timeout_seconds = max(1, int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "5")))
    return psycopg.connect(
        database_url(),
        autocommit=autocommit,
        row_factory=dict_row,
        connect_timeout=timeout_seconds,
    )


def initialize_database() -> None:
    schema = (PROJECT_ROOT / "docs" / "schema.sql").read_text(encoding="utf-8")
    with connect(autocommit=True) as connection:
        connection.execute(schema, prepare=False)


def database_health() -> dict[str, Any]:
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT current_database() AS database, current_setting('server_version') AS version"
            ).fetchone()
        return {"status": "ok", **row}
    except Exception as error:  # Health endpoints must degrade without hiding the API.
        return {"status": "error", "message": str(error)[:180]}
