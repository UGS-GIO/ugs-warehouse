"""Write the transformed topic to DuckLake — native DuckDB geom + timestamps.

Each topic = one DuckLake table. We CREATE OR REPLACE on each ingest, which
matches the `_current` snapshot semantics; DuckLake keeps prior versions via
its snapshot model so older states stay readable by snapshot id.

No WKB cast, no tz normalize — DuckLake stores native DuckDB types directly.
"""
from __future__ import annotations

import duckdb

from . import catalog
from .topics import Topic


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Overwrite the topic's DuckLake table with the transformed view."""
    alias = catalog.attach(con)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{topic.schema}")
    fqn = f"{alias}.{topic.schema}.{topic.stem}"
    con.execute(f"CREATE OR REPLACE TABLE {fqn} AS SELECT * FROM {view}")
    rows = con.execute(f"SELECT count(*) FROM {fqn}").fetchone()[0]
    print(f"[{topic.fqn}] ducklake: {rows} rows -> {fqn}")
