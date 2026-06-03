"""One-time PyIceberg SQL catalog bootstrap on mapping-db.

Idempotent. Creates:
  - Postgres schema `iceberg_catalog` (if missing)
  - PyIceberg's `iceberg_tables` + `iceberg_namespace_properties` (auto on
    first catalog connect via SqlCatalog's `init_catalog_tables=True`)
  - Iceberg namespaces: hazards, emp, gen_gis

The catalog URI must include a `search_path` option pointing at the
`iceberg_catalog` schema so PyIceberg's tables land there, e.g.:
  postgresql+psycopg://user:pass@host:5432/seamlessgeolmap?options=-csearch_path%3Diceberg_catalog%2Cpublic

Run:
  python scripts/bootstrap_catalog.py
"""
from __future__ import annotations

import sys

import psycopg

from ugs_warehouse.catalog import CATALOG_SCHEMA, CATALOG_URI, catalog


def _ensure_schema() -> None:
    if not CATALOG_URI:
        raise SystemExit("ICEBERG_CATALOG_URI not set")
    # Strip the sqlalchemy driver prefix + URI options for libpq.
    pg_dsn = CATALOG_URI.replace("postgresql+psycopg://", "postgresql://").split("?", 1)[0]
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {CATALOG_SCHEMA}")
        conn.commit()
    print(f"schema {CATALOG_SCHEMA}: ready")


def _ensure_catalog_tables() -> object:
    # Loading the catalog with init_catalog_tables (PyIceberg default) creates
    # `iceberg_tables` + `iceberg_namespace_properties` if missing.
    cat = catalog()
    print("iceberg catalog tables: ready")
    return cat


def _ensure_namespaces(cat) -> None:
    for ns in ("hazards", "emp", "gen_gis"):
        try:
            cat.create_namespace(ns)
            print(f"namespace {ns}: created")
        except Exception as e:
            print(f"namespace {ns}: exists ({type(e).__name__})")


def main() -> int:
    _ensure_schema()
    cat = _ensure_catalog_tables()
    _ensure_namespaces(cat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
