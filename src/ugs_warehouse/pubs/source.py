"""Pub metadata source — live MySQL when configured, vendored CSV snapshot otherwise.

`pubsdb` / `pubsattacheddata` are MySQL tables; in prod the catalog reads them live. The
CSVs packaged under `pubs/data/` are a self-contained snapshot fallback so the pipeline
works with no DB access. Ported from ugs-geolmap-cog-poc/catalog/pubs_source.py.

Precedence:
  1. MySQL          if PUBS_DB_URL is set   (mysql://user:pass@host:3306/dbname — env/secret only)
  2. PUBS_REPO CSVs if PUBS_REPO is set     (the ugs-publications export dir)
  3. vendored CSVs  packaged in `pubs/data/` (default, self-contained)

PUBS_DB_URL must come from env/secret — never commit credentials. Point it at a Cloud SQL
socket / proxy in prod (or wire the cloud-sql-python-connector later).
"""
from __future__ import annotations

import csv
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PUBS_DB_URL = os.environ.get("PUBS_DB_URL")
PUBS_TABLE = os.environ.get("PUBS_TABLE", "pubsdb")
ATT_TABLE = os.environ.get("PUBS_ATT_TABLE", "pubsattacheddata")


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


def _from_csv(base: str) -> list[dict]:
    repo = os.environ.get("PUBS_REPO")
    candidates = []
    if repo:
        candidates += [os.path.join(repo, f"{base}7May26.csv"), os.path.join(repo, f"{base}.csv")]
    candidates.append(os.path.join(DATA, f"{base}.csv"))  # vendored, packaged
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        raise FileNotFoundError(f"no source for {base}: tried {candidates}")
    with open(path, encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def read_pubs() -> list[dict]:
    return _from_mysql(PUBS_TABLE) if PUBS_DB_URL else _from_csv("pubsdb")


def read_attachments() -> list[dict]:
    return _from_mysql(ATT_TABLE) if PUBS_DB_URL else _from_csv("pubsattacheddata")


def source_name() -> str:
    return f"MySQL:{PUBS_TABLE}" if PUBS_DB_URL else "CSV (PUBS_REPO or vendored pubs/data/)"
