"""Pub metadata source — the live publications database, MySQL or Postgres.

`PUBS_DB_URL` is required: a MySQL URL (mysql://user:pass@host:3306/dbname), or a Postgres DSN
read through DuckDB's postgres extension. Prod points it at the publications feed on Cloud SQL
(cloudbuild.yaml). There is no file fallback: a stale snapshot published silently is worse than a
run that stops.

PUBS_DB_URL must come from env/secret — never commit credentials.
"""
from __future__ import annotations

import os

PUBS_DB_URL = os.environ.get("PUBS_DB_URL")
PUBS_TABLE = os.environ.get("PUBS_TABLE", "pubsdb")
ATT_TABLE = os.environ.get("PUBS_ATT_TABLE", "pubsattacheddata")


def _is_postgres() -> bool:
    if not PUBS_DB_URL:
        return False
    return PUBS_DB_URL.startswith(("postgres://", "postgresql://")) or "host=" in PUBS_DB_URL


def inject_pg_password(dsn: str) -> str:
    """Fold the PGPASSWORD env var into a Postgres DSN when it isn't already embedded (and isn't a
    local socket). Shared by the pubs source reader + the units PMTiles builder."""
    password = os.environ.get("PGPASSWORD")
    if not (password and "password=" not in dsn
            and not any(f"@{host}" in dsn for host in ("localhost", "127.0.0.1"))):
        return dsn
    if not dsn.startswith(("postgres://", "postgresql://")):
        return f"{dsn} password={password}"
    if "@" not in dsn:
        return f"{dsn} password={password}"
    from urllib.parse import urlparse, urlunparse
    u = urlparse(dsn)
    if u.password:
        return dsn
    netloc = f"{u.username}:{password}@{u.hostname}"
    if u.port:
        netloc += f":{u.port}"
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))


def _from_postgres(table: str) -> list[dict]:
    """Read a pubs table through DuckDB's postgres scanner.

    The connection is closed in a `finally` (like `_from_mysql`): rows are fully materialised
    into plain dicts before returning, so nothing outlives the call. A harvest calls this twice —
    pubs and attachments — and each ATTACH holds a Cloud SQL connection, so leaking them costs
    real server-side slots for the life of the process, not just Python memory.
    """
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        dsn = inject_pg_password(PUBS_DB_URL)
        con.execute(f"ATTACH '{dsn}' AS pg_pubs (TYPE POSTGRES, READ_ONLY)")

        if "." in table:
            schema, name = table.split(".", 1)
            db_table = f"pg_pubs.{schema}.{name}"
        else:
            db_table = f"pg_pubs.public.{table}"

        rows = con.execute(f"SELECT * FROM {db_table}").fetchall()
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {db_table}").fetchall()]

        out = []
        for r in rows:
            row_dict = {}
            for k, v in zip(cols, r):
                if isinstance(v, list):
                    row_dict[k] = ", ".join(str(item) for item in v if item is not None)
                elif v is None:
                    row_dict[k] = ""
                else:
                    row_dict[k] = str(v)
            out.append(row_dict)
        return out
    finally:
        con.close()


def _from_mysql(table: str) -> list[dict]:
    from urllib.parse import urlparse

    import pymysql
    u = urlparse(PUBS_DB_URL)
    con = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                          password=u.password, database=u.path.lstrip("/"),
                          charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        with con.cursor() as c:
            c.execute(f"SELECT * FROM {table}")
            # coerce to strings so downstream .strip()/.get() behave like the CSV reader
            return [{k: ("" if v is None else str(v)) for k, v in r.items()}
                    for r in c.fetchall()]
    finally:
        con.close()


def _require_db() -> None:
    if not PUBS_DB_URL:
        raise RuntimeError("PUBS_DB_URL is not set: point it at the publications database")


def read_pubs() -> list[dict]:
    if _is_postgres():
        table = os.environ.get("PUBS_TABLE", "pubs.ugspubsdraft2")
        return _from_postgres(table)
    _require_db()
    return _from_mysql(PUBS_TABLE)


def read_attachments() -> list[dict]:
    if _is_postgres():
        table = os.environ.get("PUBS_ATT_TABLE", "pubs.attached_data")
        return _from_postgres(table)
    _require_db()
    return _from_mysql(ATT_TABLE)


def source_name() -> str:
    if _is_postgres():
        table = os.environ.get("PUBS_TABLE", "pubs.ugspubsdraft2")
        return f"PostgreSQL:{table}"
    return f"MySQL:{PUBS_TABLE}" if PUBS_DB_URL else "none (PUBS_DB_URL is not set)"
