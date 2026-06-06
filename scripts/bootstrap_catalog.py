"""DuckLake catalog bootstrap on mapping-db. Idempotent.

Does:
  - ATTACH the warehouse DuckLake (auto-creates its catalog tables in Postgres
    on first ATTACH)
  - Creates DuckLake schemas: hazards, emp, gen_gis

Re-runnable: ATTACH is idempotent; CREATE SCHEMA uses IF NOT EXISTS.

Env (see catalog.py):
  DUCKLAKE_CATALOG_DSN  libpq DSN for the catalog Postgres
  DUCKLAKE_DATA_PATH    where parquet chunks live
"""
from __future__ import annotations

import sys

import duckdb

from ugs_warehouse import catalog


def main() -> int:
    con = duckdb.connect()
    alias = catalog.attach(con)
    print(f"ducklake attached as '{alias}' (data_path={catalog.DATA_PATH})")
    for s in catalog.schemas_to_ensure():
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{s}")
        print(f"ducklake schema {alias}.{s}: ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
