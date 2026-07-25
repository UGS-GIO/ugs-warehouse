"""Fetch a single `raw.raster_catalog` edition and shape it into the consumer contract record.

This is the aliasing layer between ugs-ingest #169's stored columns and the names `consume.py`
binds (see its module docstring). It runs the SELECT on the Postgres side via DuckDB's
`postgres_query` escape hatch — the same pattern as `vector/source.py` — so PostGIS functions
(`ST_AsGeoJSON`) execute server-side and geometry never has to cross as a native type.

Env: `POSTGRES_DSN` (libpq DSN; Cloud Run builds it from the Cloud SQL socket), `PGPASSWORD`.
"""
from __future__ import annotations

import json
import os
import re

import duckdb

POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN",
    "host=127.0.0.1 port=5433 dbname=seamlessgeolmap",
)
PG_ALIAS = "pg"

# item_id is ingest-authored `{piece}_{pubid}_{pubdate}`, sanitized to [a-z0-9_]. Validate before it
# reaches the (dollar-quoted, non-parameterizable) postgres_query SQL — a hard guard against injection.
_ITEM_ID_RE = re.compile(r"[a-z0-9_]+")

# raw.raster_catalog columns, aliased/cast to the contract names consume.py reads. publication_date and
# footprint_geom are transformed on the Postgres side; native_crs/bbox are parsed in Python below.
_SELECT = (
    "SELECT layer, item_id, collection, "
    "to_char(publication_date, 'YYYY-MM-DD\"T00:00:00Z\"') AS datetime, "
    "bbox_4326::text AS bbox_json, "
    "ST_AsGeoJSON(footprint_geom) AS geometry_json, "
    "native_crs, staged_cog_uri, title, description, data_type, units, "
    "ugs_author, ugs_pub_type, pub_id, is_mosaic, has_thumbnail "
    "FROM raw.raster_catalog WHERE item_id = '{item_id}'"
)


def _epsg_from_crs(native_crs: str | None) -> int | None:
    """`native_crs` is TEXT like 'EPSG:26912' (per #169 migration 001) — pull the numeric code.
    None/unparseable → None, so the STAC item just omits the projection extension."""
    if not native_crs:
        return None
    digits = re.sub(r"\D", "", native_crs)
    return int(digits) if digits else None


def _record_from_row(raw: dict) -> dict:
    """A raw SELECT row → the contract record consume.py consumes. Pure: no DB. `bbox`/`geometry`
    arrive as JSON text (jsonb::text and ST_AsGeoJSON) and are parsed here; a null footprint yields a
    null geometry (sink_stac falls back to the bbox polygon)."""
    return {
        "layer": raw["layer"],
        "item_id": raw["item_id"],
        "collection": raw["collection"],
        "datetime": raw["datetime"],
        "bbox": json.loads(raw["bbox_json"]) if raw.get("bbox_json") else None,
        "geometry": json.loads(raw["geometry_json"]) if raw.get("geometry_json") else None,
        "epsg": _epsg_from_crs(raw.get("native_crs")),
        "staged_cog_uri": raw["staged_cog_uri"],
        "title": raw.get("title"),
        "description": raw.get("description"),
        "data_type": raw.get("data_type"),
        "units": raw.get("units"),
        "ugs_author": raw.get("ugs_author"),
        "ugs_pub_type": raw.get("ugs_pub_type"),
        "pub_id": raw.get("pub_id"),
        "is_mosaic": raw.get("is_mosaic"),
        "has_thumbnail": bool(raw.get("has_thumbnail")),
    }


def _connect() -> duckdb.DuckDBPyConnection:
    """DuckDB with Postgres ATTACHed read-only. No spatial extension needed — ST_AsGeoJSON runs on the
    Postgres side inside postgres_query, so geometry comes back as plain GeoJSON text."""
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    password = os.environ.get("PGPASSWORD")
    dsn = f"{POSTGRES_DSN} password={password}" if password else POSTGRES_DSN
    con.execute(f"ATTACH '{dsn}' AS {PG_ALIAS} (TYPE POSTGRES, READ_ONLY)")
    return con


def fetch_record(item_id: str) -> dict | None:
    """The contract record for one raster edition, or None if no such `item_id`. Fetches by the exact
    edition id (append-only — every edition is promoted to its own dated COG/item), so no is_current
    filter. Raises ValueError on a malformed item_id."""
    if not item_id or not _ITEM_ID_RE.fullmatch(item_id):
        raise ValueError(f"malformed item_id: {item_id!r}")
    con = _connect()
    try:
        pg_sql = _SELECT.format(item_id=item_id)
        cur = con.execute("SELECT * FROM postgres_query(?, ?)", [PG_ALIAS, pg_sql])
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return _record_from_row(dict(zip(cols, row)))
    finally:
        con.close()
