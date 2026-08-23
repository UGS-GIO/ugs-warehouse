"""Vector-topic STAC item — emitted through the shared core into the
`ugs-serving-topics` catalog of the one catalog, under a per-mart-schema sub-collection
(`ugs-serving-topics/<schema>`), the same one-level nesting pubs use for series.

Assets: GeoParquet archive (`data`), PMTiles (`pmtiles`), and — only in the review catalog —
a DuckLake table (`ducklake`, a gs:// locator read by DuckDB, not a browser). The DuckLake asset
is withheld from the PUBLIC catalog: no public consumer can read it (gs:// + private bucket IAM,
and resolving a DuckLake table needs the private catalog DSN), so advertising it there is a
dead link. A web-map-links `pmtiles` link makes STAC Browser v4+ render the actual layer.
bbox/row_count come from the transformed view.
"""
from __future__ import annotations

import datetime
import sys

import duckdb

from ..core import config, gcs, stac
from . import ducklake, introspect
from .topics import Topic

# The nesting catalog. A topic's own collection is its dbt mart schema, one level down — the
# collection id is the bare schema (`hazards`), matching the layout segment. The collection's
# title is that name prettified; nothing else is authored for it (see core.stac).
CATALOG = stac.SERVING_TOPICS_CATALOG
PARQUET_MIME = config.PARQUET_MIME
PMTILES_MIME = config.PMTILES_MIME


def collection_path(schema: str) -> str:
    """GCS layout path of a mart schema's sub-collection."""
    return f"{CATALOG}/{schema}"


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


def _publication_datetime(con: duckdb.DuckDBPyConnection, view: str) -> str | None:
    """The data's own publication date, when the serving table carries one.

    `_publication_date` is the date entered on the ugs-ingest upload form; dataELT#504 put it on
    the serving-table enrichment allowlist. Dating from the ingest clock instead made Utah counties
    and Quaternary faults seven minutes apart, and gave every ISO record a zero-width temporal
    extent at the run time.

    PROBED, not assumed. Tables gain the column only as each is republished, and a topic published
    off publish.sh's fallback paths may never gain it — an unconditional MAX() would raise and take
    that item's STAC + ISO record down entirely, which is worse than a wrong-but-present date.

    MAX because a serving table holds the current rows from potentially several loads: the item is
    as new as the newest publication in it.
    """
    # `write()` takes no connection when the caller supplies bbox + row_count itself; nothing to
    # probe then, same as a table without the column.
    if con is None:
        return None
    cols = {r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()}
    if "_publication_date" not in cols:
        return None
    value = con.execute(f'SELECT MAX("_publication_date") FROM {view}').fetchone()[0]
    if value is None:                       # column present, nobody filled it in
        return None
    # The dbt source YAML doesn't declare a type, so take date, timestamp or text as they come.
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value)
        except ValueError:
            print(f"[{view}] _publication_date not a date: {value!r} — dating from the clock",
                  file=sys.stderr)
            return None
    if isinstance(value, datetime.datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    elif isinstance(value, datetime.date):
        stamp = datetime.datetime.combine(value, datetime.time(), tzinfo=datetime.UTC)
    else:
        print(f"[{view}] _publication_date is a {type(value).__name__} — dating from the clock",
              file=sys.stderr)
        return None
    return stamp.astimezone(datetime.UTC).isoformat()


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
    # The data's date when it has one; the clock only as a fallback (#65).
    item_dt = _publication_datetime(con, view) or datetime.datetime.now(datetime.UTC).isoformat()
    md = metadata or {}

    archive_path = f"{config.ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
    pmtiles_path = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    pmtiles_url = config.public_url(pmtiles_path)

    # Precedence for title/description: hand-authored override (ops console) > registry metadata >
    # prior published value. So an operator's edit wins + survives reingest.
    ov = stac.manual_override(topic.stem)
    props = {
        "title": ov.get("title") or md.get("display_name") or title or stac.prettify(topic.stem),
        "ugs:dbt_schema": topic.schema,
        "ugs:layer": topic.layer,
        "ugs:row_count": rc,
    }
    # Durable row key. Stamp `ugs:primary_key` when the served view carries ugs_key (an ARMED
    # topic), so consumers know which column is the stable per-row identity — enables row
    # commenting/deep-links keyed on it. Absent on dormant topics → consumers keep their fallback.
    # Presence-probed (con may be None on the bbox/row_count-supplied path).
    if con is not None and introspect.has_ugs_key(con, view):
        props["ugs:primary_key"] = introspect.UGS_KEY
    # Content fingerprint (skip-unchanged ingest). Lets a later `--skip-unchanged` run detect that
    # nothing changed and skip the rebuild. Absent when the caller didn't compute one.
    if content_hash:
        props["ugs:content_hash"] = content_hash
    # registry `description` → STAC `description` (ISO export renames it to <gmd:abstract>).
    # Preserve-on-empty: registry descriptions are often missing, so when this submit has none, keep
    # whatever the published item already had instead of blanking it (last-non-empty wins).
    desc = ov.get("description") or md.get("description") or description \
        or stac.prior_property(collection_path(topic.schema), topic.stem, "description")
    if desc:
        props["description"] = desc
    # Curated catalog metadata (raw.schema_registry) — flows into STAC + ISO.
    if md.get("keywords"):
        props["keywords"] = list(md["keywords"])
    # `summary_fields`: the columns that identify a row, in order. Consumers lead with these when
    # they can't show every column (the viewer's phone cards); absent → they fall back to a guess.
    for src_key, prop in (("iso_topic_category", "ugs:topic_category"),
                          ("use_constraints", "ugs:use_constraints"),
                          ("lineage", "ugs:lineage"),
                          ("point_of_contact", "ugs:point_of_contact"),
                          ("summary_fields", "ugs:summary_fields")):
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
        # Aspatial related tables (e.g. UCRC boxes/photos/attachments) materialised as Parquet,
        # each carrying its own `ugs:foreign_keys` (child → this topic) + `table:columns`.
        # Registry-driven (raw.schema_registry.relationships); absent for most topics.
        **rel_assets,
    }
    # DuckLake table locator — REVIEW CATALOG ONLY. It's a gs:// table id, not a fetchable object:
    # a browser can't open gs://, the bucket is private (IAM-gated), and even with the parquet
    # chunks a consumer can't materialise the table without the private DuckLake catalog DSN. So the
    # public catalog would only advertise a dead link. The review app can reach private assets (via
    # signed URLs), so it's meaningful there.
    if config.IS_REVIEW_CATALOG:
        ducklake_uri = f"{ducklake.DATA_PATH.rstrip('/')}/{topic.schema}/{topic.stem}"
        assets["ducklake"] = {"href": ducklake_uri, "type": "application/x-ducklake-table",
                              "roles": ["data"], "title": "DuckLake table (native geometry)"}
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
        item_id=topic.stem, collection=topic.schema,
        collection_path=collection_path(topic.schema),
        geometry=stac.bbox_polygon(bb), bbox=bb, datetime_iso=item_dt,
        properties=props,
        assets=assets,
        # `related` links (the FK graph) ride alongside the web-map pmtiles link.
        # featureserv binds one collection per topic, named after the STAC item id — so the
        # queryable OGC API Features endpoint is addressable here and nowhere else in the catalog.
        extra_links=[stac.pmtiles_link(pmtiles_url, [topic.stem]),
                     {"rel": "service", "href": f"{config.PGF_BASE_URL}/collections/{topic.stem}",
                      "type": "application/json", "title": "OGC API Features collection"},
                     *rel_links],
        stac_extensions=exts,
        proj_epsg=4326,  # transform reprojects every topic to 4326
    )
    stac.attach_renders(item)  # ugs-styles GL style -> render extension (graceful if none)
    stac.attach_classification(item)  # classification:classes from the style's categories (graceful)
    stac.attach_iso(item)  # ISO 19139 sidecar + `metadata` asset (gov clearinghouses)
    path = stac.write_item(item)
    print(f"[{topic.fqn}] stac: {config.public_url(path)}")
