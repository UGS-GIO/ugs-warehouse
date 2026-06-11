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

    is_gcs = DATA_PATH.startswith(("gs://", "gcs://"))

    # For GCS we route DuckLake's data-file IO through obstore via fsspec, NOT
    # httpfs: httpfs only auths to GCS with HMAC keys, which org policy blocks.
    # We skip loading httpfs on the GCS path so it can't shadow the gs:// scheme
    # the fsspec filesystem handles. (httpfs is still loaded for s3/other.)
    exts = ("spatial", "postgres", "ducklake") if is_gcs else (
        "httpfs", "spatial", "postgres", "ducklake"
    )
    for ext in exts:
        con.execute(f"INSTALL {ext};")
        con.execute(f"LOAD {ext};")

    if is_gcs:
        # DuckLake honors a Python-registered fsspec filesystem for its DATA_PATH
        # writes (duckdb/ducklake#628). obstore's fsspec adapter authenticates via
        # ADC — no HMAC. Python-client only, which fits our FastAPI/CLI runtime.
        from fsspec import filesystem
        from obstore.fsspec import register as register_obstore
        register_obstore("gs")
        con.register_filesystem(filesystem("gs"))

    # Check if override is enabled
    override = os.environ.get("OVERRIDE_DATA_PATH", "False") == "True"
    
    cmd = (
        f"ATTACH 'ducklake:postgres:{CATALOG_DSN}' AS {CATALOG_ALIAS} "
        f"(DATA_PATH '{DATA_PATH}', METADATA_SCHEMA '{METADATA_SCHEMA}'"
    )
    if override:
        cmd += ", OVERRIDE_DATA_PATH TRUE"
    cmd += ")"
    
    con.execute(cmd)
    return CATALOG_ALIAS


def schemas_to_ensure() -> tuple[str, ...]:
    """DuckLake schemas the bootstrap creates (matches mart schemas)."""
    return ("hazards", "emp", "gen_gis")
