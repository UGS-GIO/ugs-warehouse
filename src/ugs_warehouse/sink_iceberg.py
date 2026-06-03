"""Write the transformed topic to Iceberg, with WKB BLOB geometry.

Catalog: PyIceberg SQL catalog on `mapping-db` (`iceberg_catalog` schema).
Data:    GCS under `ICEBERG_WAREHOUSE_PATH`.

Each topic = one Iceberg table. We overwrite on each ingest — matches the
`_current` snapshot semantics. Iceberg keeps prior versions via its snapshot
model, so older states stay citable by snapshot id.
"""
from __future__ import annotations

import duckdb
import pyarrow as pa
from pyiceberg.exceptions import NoSuchNamespaceError, NoSuchTableError

from .catalog import catalog, iceberg_namespace
from .topics import Topic


def _ensure_namespace(cat, ns: str) -> None:
    try:
        cat.create_namespace(ns)
    except NoSuchNamespaceError:
        pass
    except Exception:
        # Namespace already exists or backend reports it differently — proceed.
        pass


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Overwrite the topic's Iceberg table with the transformed view."""
    arrow_iceberg: pa.Table = con.execute(
        f"SELECT * REPLACE (ST_AsWKB(geom) AS geom) FROM {view}"
    ).arrow()

    cat = catalog()
    ns = iceberg_namespace(topic.schema)
    _ensure_namespace(cat, ns)

    ident = (ns, topic.stem)
    try:
        tbl = cat.load_table(ident)
        tbl.overwrite(arrow_iceberg)
    except NoSuchTableError:
        tbl = cat.create_table(identifier=ident, schema=arrow_iceberg.schema)
        tbl.append(arrow_iceberg)

    print(
        f"[{topic.fqn}] iceberg: {arrow_iceberg.num_rows} rows -> "
        f"{ns}.{topic.stem}"
    )
