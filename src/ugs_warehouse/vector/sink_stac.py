"""Vector-topic STAC item — emitted through the shared core into the
`ugs-serving-topics` collection of the one catalog.

Assets: GeoParquet archive (`data`), PMTiles (`pmtiles`), DuckLake table (`ducklake`,
a gs:// locator read by DuckDB, not a browser). A web-map-links `pmtiles` link makes
STAC Browser v4+ render the actual layer. bbox/row_count come from the transformed view.
"""
from __future__ import annotations

import datetime

import duckdb

from ..core import config, gcs, stac
from . import ducklake
from .topics import Topic

COLLECTION = "ugs-serving-topics"
PARQUET_MIME = config.PARQUET_MIME
PMTILES_MIME = config.PMTILES_MIME


def _bbox(con: duckdb.DuckDBPyConnection, view: str) -> list[float]:
    row = con.execute(f"""
        SELECT MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
               MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
        FROM {view}
    """).fetchone()
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])]


def _row_count(con: duckdb.DuckDBPyConnection, view: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {view}").fetchone()[0])


def _table_type(duck_type: str) -> str:
    """DuckDB column type → STAC Table extension type string."""
    t = duck_type.upper()
    if t.startswith("GEOMETRY"):
        return "geometry"
    if "INT" in t or t == "HUGEINT":
        return "integer"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return "number"
    if t in ("BOOLEAN", "BOOL"):
        return "boolean"
    if t.startswith("TIMESTAMP"):
        return "datetime"
    if t == "DATE":
        return "date"
    if "CHAR" in t or t in ("VARCHAR", "TEXT", "STRING"):
        return "string"
    return duck_type.lower()


def _table_columns(con: duckdb.DuckDBPyConnection, view: str) -> list[dict]:
    """`table:columns` describing the GeoParquet — the real materialized columns + their types."""
    return [{"name": r[0], "type": _table_type(str(r[1]))}
            for r in con.execute(f"DESCRIBE {view}").fetchall()]


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str,
          *, title: str | None = None, description: str | None = None,
          metadata: dict | None = None, bbox: list[float] | None = None,
          row_count: int | None = None, related: dict | None = None,
          content_hash: str | None = None) -> None:
    rel = related or {}
    rel_assets = rel.get("assets") or {}
    rel_links = rel.get("links") or []
    rel_fks = rel.get("foreign_keys") or []
    bb = bbox if bbox is not None else _bbox(con, view)
    rc = row_count if row_count is not None else _row_count(con, view)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    md = metadata or {}

    archive_path = f"{config.ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
    pmtiles_path = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    pmtiles_url = config.public_url(pmtiles_path)
    # ducklake stays a gs:// locator — read by DuckDB, not a browser.
    ducklake_uri = f"{ducklake.DATA_PATH.rstrip('/')}/{topic.schema}/{topic.stem}"

    # Precedence for title/description: hand-authored override (ops console) > registry metadata >
    # prior published value. So an operator's edit wins + survives reingest.
    ov = stac.manual_override(topic.stem)
    props = {
        "title": ov.get("title") or md.get("display_name") or title or stac.prettify(topic.stem),
        "ugs:dbt_schema": topic.schema,
        "ugs:layer": topic.layer,
        "ugs:row_count": rc,
    }
    # Content fingerprint (skip-unchanged ingest). Lets a later `--skip-unchanged` run detect that
    # nothing changed and skip the rebuild. Absent when the caller didn't compute one.
    if content_hash:
        props["ugs:content_hash"] = content_hash
    # registry `description` → STAC `description` (ISO export renames it to <gmd:abstract>).
    # Preserve-on-empty: registry descriptions are often missing, so when this submit has none, keep
    # whatever the published item already had instead of blanking it (last-non-empty wins).
    desc = ov.get("description") or md.get("description") or description \
        or stac.prior_property(COLLECTION, topic.stem, "description")
    if desc:
        props["description"] = desc
    # Curated catalog metadata (raw.schema_registry) — flows into STAC + ISO.
    if md.get("keywords"):
        props["keywords"] = list(md["keywords"])
    for src_key, prop in (("iso_topic_category", "ugs:topic_category"),
                          ("use_constraints", "ugs:use_constraints"),
                          ("lineage", "ugs:lineage"),
                          ("point_of_contact", "ugs:point_of_contact")):
        if md.get(src_key):
            props[prop] = md[src_key]

    # GeoParquet archive. `table:columns` is the standard STAC Table extension. `ugs:foreign_keys`
    # (this topic's outgoing FKs) is a UGS-prefixed custom field — the FK join detail has no STAC
    # extension, so it's namespaced per STAC best practice (full-spec-compliant: prefixed, not
    # declared in stac_extensions since there's no resolvable schema). Shape mirrors Frictionless.
    data_asset = {"href": config.public_url(archive_path), "type": PARQUET_MIME,
                  "roles": ["data"], "title": "GeoParquet archive (native geometry)",
                  "table:columns": _table_columns(con, view)}
    if rel_fks:
        data_asset["ugs:foreign_keys"] = rel_fks

    assets = {
        "data": data_asset,
        "pmtiles": {"href": pmtiles_url, "type": PMTILES_MIME,
                    "roles": ["visual"], "title": "PMTiles vector tiles"},
        "ducklake": {"href": ducklake_uri, "type": "application/x-ducklake-table",
                     "roles": ["data"], "title": "DuckLake table (native geometry)"},
        # Aspatial related tables (e.g. UCRC boxes/photos/attachments) materialised as Parquet,
        # each carrying its own `ugs:foreign_keys` (child → this topic) + `table:columns`.
        # Registry-driven (raw.schema_registry.relationships); absent for most topics.
        **rel_assets,
    }
    # Rendered preview PNG (styled PMTiles → image), written independently by the ugs-topics-thumbs
    # job. Presence-driven, exactly like the pubs cover/thumbnail: stamp the asset iff the PNG exists,
    # so the catalog shows a real styled preview for topics that have one (sand placeholder otherwise).
    thumb_path = f"{config.THUMBS_PREFIX}/{topic.stem}/{topic.stem}.png"
    if gcs.exists(thumb_path):
        assets["thumbnail"] = {"href": config.public_url(thumb_path), "type": "image/png",
                               "roles": ["thumbnail"], "title": "Styled preview"}
    # Table extension is in play iff any asset describes its columns.
    exts = [stac.WEB_MAP_LINKS_EXT]
    if any("table:columns" in a for a in assets.values()):
        exts.append(stac.TABLE_EXT)

    item = stac.build_item(
        item_id=topic.stem, collection=COLLECTION,
        geometry=stac.bbox_polygon(bb), bbox=bb, datetime_iso=now,
        properties=props,
        assets=assets,
        # `related` links (the FK graph) ride alongside the web-map pmtiles link.
        extra_links=[stac.pmtiles_link(pmtiles_url, [topic.stem]), *rel_links],
        stac_extensions=exts,
        proj_epsg=4326,  # transform reprojects every topic to 4326
    )
    stac.attach_renders(item)  # ugs-styles GL style -> render extension (graceful if none)
    stac.attach_classification(item)  # classification:classes from the style's categories (graceful)
    stac.attach_iso(item)  # ISO 19139 sidecar + `metadata` asset (gov clearinghouses)
    path = stac.write_item(item)
    print(f"[{topic.fqn}] stac: {config.public_url(path)}")
