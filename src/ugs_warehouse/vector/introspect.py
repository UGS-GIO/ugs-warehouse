"""Column introspection for per-topic-conditional ugs_key consumption.

`ugs_key` (the pipeline-minted durable row key) exists only on ARMED topics — those whose
dataELT/ugs-ingest declared a `durable_key`. Most topics are still dormant and carry only the
ephemeral `feature_id` (a Hilbert row-number). Every sink probes for `ugs_key` here and falls
back to the legacy `feature_id` path when it is absent, so warehouse consumption is safe to ship
while only a handful of topics are armed.

Single source of truth for the column names + the presence check (previously inlined ad hoc in
sink_stac's `DESCRIBE` probe).
"""
from __future__ import annotations

import duckdb

UGS_KEY = "ugs_key"
FEATURE_ID = "feature_id"


def column_names(con: duckdb.DuckDBPyConnection, view: str) -> list[str]:
    """Ordered column names of a relation the pipeline controls (a bare table/view name)."""
    return [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]


def has_ugs_key(con: duckdb.DuckDBPyConnection, view: str) -> bool:
    """True when the relation carries the durable ugs_key column (i.e. the topic is armed)."""
    return UGS_KEY in column_names(con, view)


def id_column(con: duckdb.DuckDBPyConnection, view: str) -> str:
    """The served feature id for this topic: ugs_key when armed, else the ephemeral feature_id."""
    return UGS_KEY if has_ugs_key(con, view) else FEATURE_ID
