"""PyIceberg SQL catalog wired to the existing `mapping-db` Postgres.

Catalog metadata lives in a dedicated schema (`iceberg_catalog`) on the same
Cloud SQL instance that hosts the dbt marts. No new database. Data files
(parquet manifests + data) live on GCS under WAREHOUSE_PATH.

Env:
  ICEBERG_CATALOG_URI    SQLAlchemy URI for the catalog Postgres
                         e.g. postgresql+psycopg://user:pass@127.0.0.1:5432/seamlessgeolmap
  ICEBERG_WAREHOUSE_PATH gs://ut-dnr-ugs-maps-prod-public/warehouse/iceberg/
"""
from __future__ import annotations

import os

from pyiceberg.catalog import load_catalog
from pyiceberg.catalog.sql import SqlCatalog

CATALOG_NAME = "ugs"
CATALOG_SCHEMA = "iceberg_catalog"  # Postgres schema that holds catalog tables
CATALOG_URI = os.environ.get("ICEBERG_CATALOG_URI", "")
WAREHOUSE_PATH = os.environ.get(
    "ICEBERG_WAREHOUSE_PATH",
    "gs://ut-dnr-ugs-maps-prod-public/warehouse/iceberg/",
)


def catalog() -> SqlCatalog:
    """Open the warehouse Iceberg SQL catalog."""
    if not CATALOG_URI:
        raise SystemExit(
            "ICEBERG_CATALOG_URI not set — point it at the mapping-db Postgres "
            "(see README)."
        )
    return load_catalog(
        CATALOG_NAME,
        type="sql",
        uri=CATALOG_URI,
        warehouse=WAREHOUSE_PATH,
    )


def iceberg_namespace(topic_schema: str) -> str:
    """Iceberg namespace for a dbt mart schema.

    One Iceberg namespace per dbt mart schema (`hazards`, `emp`, `gen_gis`),
    keeping Iceberg table FQNs aligned with the source `{schema}.{topic}_current`.
    """
    return topic_schema
