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


def _normalize_tz(table: pa.Table) -> pa.Table:
    """Cast any tz-aware timestamp columns to UTC.

    Iceberg's timestamptz type stores UTC only; PyIceberg rejects other zones
    (e.g. the DuckDB session zone leaking through from Postgres TIMESTAMPTZ).
    """
    new_fields: list[pa.Field] = []
    new_arrays: list[pa.Array] = []
    changed = False
    for field, arr in zip(table.schema, table.columns):
        if pa.types.is_timestamp(field.type) and field.type.tz not in (None, "UTC"):
            new_type = pa.timestamp(field.type.unit, tz="UTC")
            new_fields.append(
                pa.field(field.name, new_type, field.nullable, field.metadata)
            )
            new_arrays.append(arr.cast(new_type))
            changed = True
        else:
            new_fields.append(field)
            new_arrays.append(arr)
    return pa.Table.from_arrays(new_arrays, schema=pa.schema(new_fields)) if changed else table


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Overwrite the topic's Iceberg table with the transformed view."""
    arrow_iceberg: pa.Table = _normalize_tz(
        con.execute(
            f"SELECT * REPLACE (ST_AsWKB(geom) AS geom) FROM {view}"
        ).fetch_arrow_table()
    )

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
