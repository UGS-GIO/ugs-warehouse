"""DuckLake catalog on Postgres (mapping-db).

Catalog metadata lives in a DuckLake-managed Postgres database; data parquet
chunks land in GCS under DATA_PATH. The catalog tables are auto-created by
the DuckLake extension on first ATTACH.

Env:
  DUCKLAKE_CATALOG_DSN  libpq DSN for the catalog Postgres
                        e.g. host=127.0.0.1 port=5433 dbname=seamlessgeolmap user=... password=...
  DUCKLAKE_DATA_PATH    where parquet chunks live; default GCS prod path
"""
from __future__ import annotations

import os

import duckdb

CATALOG_ALIAS = "warehouse"
CATALOG_DSN = os.environ.get("DUCKLAKE_CATALOG_DSN", "")
DATA_PATH = os.environ.get(
    "DUCKLAKE_DATA_PATH",
    "gs://ut-dnr-ugs-maps-prod-public/warehouse/ducklake/",
)
# Postgres schema DuckLake stores its metadata tables in. Must be a schema the
# catalog DSN user can write to (mapping-db prod: `schema_owner` on
# `ducklake_catalog`). DuckLake default is `main`; we pin it so the warehouse
# never tries to create catalog tables in `public`.
METADATA_SCHEMA = os.environ.get("DUCKLAKE_METADATA_SCHEMA", "ducklake_catalog")


def attach(con: duckdb.DuckDBPyConnection) -> str:
    """Attach the warehouse DuckLake to a DuckDB connection (idempotent).

    Returns the alias to use in fully-qualified references, e.g.
    f'{alias}.{schema}.{table}'.
    """
    if not CATALOG_DSN:
        raise SystemExit(
            "DUCKLAKE_CATALOG_DSN not set — point at the mapping-db Postgres"
        )
    # Idempotent: if already attached on this con, just return the alias.
    row = con.execute(
        "SELECT 1 FROM duckdb_databases() WHERE database_name = ?",
        [CATALOG_ALIAS],
    ).fetchone()
    if row:
        return CATALOG_ALIAS
    for ext in ("httpfs", "spatial", "postgres", "ducklake"):
        con.execute(f"INSTALL {ext};")
        con.execute(f"LOAD {ext};")
    con.execute(
        f"ATTACH 'ducklake:postgres:{CATALOG_DSN}' AS {CATALOG_ALIAS} "
        f"(DATA_PATH '{DATA_PATH}', METADATA_SCHEMA '{METADATA_SCHEMA}')"
    )
    return CATALOG_ALIAS


def schemas_to_ensure() -> tuple[str, ...]:
    """DuckLake schemas the bootstrap creates (matches mart schemas)."""
    return ("hazards", "emp", "gen_gis")
