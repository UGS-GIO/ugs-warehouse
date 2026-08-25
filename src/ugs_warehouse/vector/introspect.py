"""Column introspection for per-topic-conditional ugs_key consumption.

`ugs_key` (the pipeline-minted durable row key) exists only on ARMED topics — those whose
dataELT/ugs-ingest declared a `durable_key`. Most topics are still dormant and carry only the
ephemeral `feature_id` (a Hilbert row-number). Every sink probes for `ugs_key` here so warehouse
consumption is safe to ship while only a handful of topics are armed.

What armed topics get from `ugs_key` in this PR: the DuckLake MERGE keys on it (delta upserts),
STAC advertises it as `ugs:primary_key`, and it rides along as a plain attribute in the tiles /
GeoParquet / OGC output. What does NOT change here: the feature **id** exposed to consumers stays
`feature_id` in both the PMTiles MVT id and the featureserv OGC id — flipping the primary id to
`ugs_key` has to land together with the viewer's `download.ts` id-column switch (and an AGOL
objectid-range decision), so that's a deliberate follow-up, not this PR.

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


def column_schema(con: duckdb.DuckDBPyConnection, view: str) -> list[tuple[str, str]]:
    """Ordered (name, type) pairs — DESCRIBE returns column_name, column_type as its first two
    fields. Used to detect a type change under an unchanged name (a rename-free `VARCHAR`→`BIGINT`),
    which a names-only comparison misses."""
    return [(r[0], r[1]) for r in con.execute(f"DESCRIBE {view}").fetchall()]


def has_ugs_key(con: duckdb.DuckDBPyConnection, view: str) -> bool:
    """True when the relation carries the durable ugs_key column (i.e. the topic is armed)."""
    return UGS_KEY in column_names(con, view)
