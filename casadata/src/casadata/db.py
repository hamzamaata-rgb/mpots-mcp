"""Connexion DuckDB, initialisation du schéma et helpers d'accès."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import duckdb

from .config import KNOWN_SOURCES, SETTINGS


def schema_sql() -> str:
    return importlib.resources.files("casadata").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Ouvre (et initialise si besoin) la base."""
    path = Path(db_path) if db_path else SETTINGS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    init_schema(conn)
    return conn


def connect_memory() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(schema_sql())
    _seed_sources(conn)


def _seed_sources(conn: duckdb.DuckDBPyConnection) -> None:
    for s in KNOWN_SOURCES:
        conn.execute(
            """
            INSERT INTO source (code, name, kind, base_url)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM source WHERE code = ?)
            """,
            [s["code"], s["name"], s["kind"], s.get("base_url"), s["code"]],
        )


def source_id(conn: duckdb.DuckDBPyConnection, code: str) -> int:
    row = conn.execute("SELECT source_id FROM source WHERE code = ?", [code]).fetchone()
    if row is None:
        raise KeyError(f"source inconnue: {code!r} — la déclarer dans config.KNOWN_SOURCES")
    return row[0]


def start_run(
    conn: duckdb.DuckDBPyConnection,
    source_code: str,
    method: str,
    scope: str | None = None,
    raw_path: str | None = None,
) -> int:
    sid = source_id(conn, source_code)
    row = conn.execute(
        """
        INSERT INTO scrape_run (source_id, scope, method, raw_path)
        VALUES (?, ?, ?, ?) RETURNING run_id
        """,
        [sid, scope, method, raw_path],
    ).fetchone()
    return row[0]


def finish_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    status: str = "success",
    pages_fetched: int = 0,
    records_parsed: int = 0,
    records_failed: int = 0,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE scrape_run
        SET finished_at = current_timestamp, status = ?, pages_fetched = ?,
            records_parsed = ?, records_failed = ?, notes = ?
        WHERE run_id = ?
        """,
        [status, pages_fetched, records_parsed, records_failed, notes, run_id],
    )
