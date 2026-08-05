"""Relationships — project the upstream FK registry into the STAC catalog, using standards.

The authoritative foreign keys live in `raw.schema_registry.relationships` (declared/auto-detected
by ugs-ingest, enforced by dbt `relationships` tests). The warehouse does NOT redefine them; it
reads that registry per topic and emits two well-known vocabularies onto the topic's STAC item:

  * STAC `rel:"related"` links — the relationship GRAPH (item ↔ item). STAC Browser renders these.
    This is the STAC-native, fully-spec-compliant part of the relationship.
  * `ugs:foreign_keys` — the JOIN detail (which columns reference what). The FK join detail has no
    STAC extension, so it's a UGS-prefixed custom field (STAC best practice for non-standard fields:
    namespace, don't pollute the root). Value shape mirrors Frictionless Table Schema foreignKeys.
    Placed on the asset that is the FK *source*. Not declared in stac_extensions (no schema to
    resolve) — a prefixed extra field is spec-legal without it.
  * STAC Table extension `table:columns` — standard column description for the related Parquet.

A foreign key is declared on the CHILD pointing at the parent (`targetDomainTopic`). So for a topic
T being ingested we resolve:
  - T's OUTGOING FKs (T references X): a `related` link to X's item + a `ugs:foreign_keys` entry on
    T's own data asset.
  - T's INCOMING FKs (a child references T):
      · spatial child (has its own STAC item): just a `related` link to it.
      · aspatial child (no geometry → no item of its own, e.g. UCRC boxes/photos/attachments):
        materialise the child `_current` table to CDN Parquet as a `roles:["data","related"]` asset
        on T, carrying its own `ugs:foreign_keys` (child → T) + `table:columns`.

`domain_topic` → physical table is `{target_schema}.{domain_topic}_current` (registry columns).
Best-effort throughout: a missing registry / grant / table is logged and skipped, never sinks the
parent ingest (matches `source.read_metadata`).
"""
from __future__ import annotations

import json
import os
import re
import tempfile

from ..core import config, gcs, identifiers, stac
from . import sink_stac, source
from .topics import Topic

PARQUET_MIME = config.PARQUET_MIME
_GEOM_RE = re.compile(r"geometr|geography", re.IGNORECASE)


def _q(s: str) -> str:
    """SQL string literal. domain_topic is validated `^[a-z0-9_]+$` upstream, but escape anyway."""
    return "'" + s.replace("'", "''") + "'"


def _pg(con, pg_sql: str) -> list[tuple]:
    """Run native Postgres SQL through the DuckDB postgres scanner (same path as read_metadata)."""
    return con.execute("SELECT * FROM postgres_query(?, ?)", [source.PG_ALIAS, pg_sql]).fetchall()


def _cols(rel: dict, which: str) -> list[str]:
    """Source/target columns of one relationship — composite (`{which}Columns`) or single."""
    arr = rel.get(f"{which}Columns")
    if arr:
        return [c for c in arr if c]
    one = rel.get(f"{which}Column")
    return [one] if one else []


def _foreign_key(rel: dict) -> dict | None:
    """A Frictionless Table Schema foreignKey: this asset's `fields` reference `resource.fields`."""
    src, tgt = _cols(rel, "source"), _cols(rel, "target")
    target = rel.get("targetDomainTopic")
    if not (src and tgt and target):
        return None
    return {"fields": src, "reference": {"resource": target, "fields": tgt}}


def _related_link(target_stem: str, schema: str, title: str | None = None) -> dict:
    """A STAC `related` link to a serving-topic item (absolute CDN href — no relative-depth math).

    `schema` is the target's mart schema: items are nested per schema, so a link can't be built
    from the stem alone (see `_target_schemas`)."""
    href = config.public_url(
        stac.item_object_path(sink_stac.collection_path(schema), target_stem))
    return {"rel": "related", "href": href, "type": "application/geo+json",
            "title": title or stac.prettify(target_stem)}


def _target_schemas(con, stems: list[str]) -> dict[str, str]:
    """{domain_topic: mart schema} for FK targets — one registry read, not one per link.

    Items live under `ugs-serving-topics/<schema>/`, so a target whose schema the registry doesn't
    carry can't be addressed; `resolve` drops that link rather than emit a guessed 404."""
    if not stems:
        return {}
    in_list = ",".join(_q(s) for s in stems)
    rows = _pg(con, "SELECT domain_topic, target_schema FROM raw.schema_registry "
                    f"WHERE domain_topic IN ({in_list})")
    return {t: s for t, s in rows if t and s}


def _has_geometry(business_schema: dict) -> bool:
    """True if any business-schema column is a geometry/geography type (→ the topic is spatial)."""
    for spec in (business_schema or {}).values():
        t = spec.get("type", "") if isinstance(spec, dict) else spec
        if _GEOM_RE.search(str(t)):
            return True
    return False


def _table_columns(business_schema: dict) -> list[dict]:
    """STAC Table extension `table:columns` from the registry's business_schema."""
    cols: list[dict] = []
    for name, spec in (business_schema or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        col = {"name": name}
        if spec.get("type"):
            col["type"] = str(spec["type"])
        if spec.get("description"):
            col["description"] = spec["description"]
        cols.append(col)
    return cols


def _materialize_child(con, child_topic: str, schema: str, display: str | None,
                       rel: dict, business_schema: dict, parent_stem: str) -> dict | None:
    """Archive an aspatial child `_current` table to CDN Parquet; return its related STAC asset."""
    table = f"{child_topic}_current"
    path = f"{config.ARCHIVE_PREFIX}/{parent_stem}/related/{child_topic}.parquet"
    try:
        # Both come from raw.schema_registry and are interpolated below. `resolve`'s docstring
        # claims domain_topic is validated upstream; nothing here enforces that.
        identifiers.require_identifier("related schema", schema)
        identifiers.require_identifier("related child_topic", child_topic)
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, f"{table}.parquet")
            con.execute(
                f'COPY (SELECT * FROM {source.PG_ALIAS}."{schema}"."{table}") '
                f"TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            gcs.upload(local, path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:  # noqa: BLE001 — related data is best-effort; never sink the parent
        print(f"[{parent_stem}] related '{child_topic}' ({schema}.{table}) SKIP: {e}")
        return None
    asset = {
        "href": config.public_url(path),
        "type": PARQUET_MIME,
        "roles": ["data", "related"],
        "title": display or stac.prettify(child_topic),
    }
    fk = _foreign_key(rel)
    if fk:
        asset["ugs:foreign_keys"] = [fk]   # UGS-prefixed: child columns → this parent (Frictionless shape)
    cols = _table_columns(business_schema)
    if cols:
        asset["table:columns"] = cols
    return asset


def resolve(topic: Topic) -> dict:
    """Registry-driven relationships for `topic`, as standard STAC/Frictionless pieces:

        {"assets": {key: asset}, "links": [related_link, ...], "foreign_keys": [fk, ...]}

    `assets` merge into the item's assets, `links` into its links, `foreign_keys` onto the item's
    own `data` asset. Empty pieces when the registry/grant/relationships are absent (incremental).
    """
    empty = {"assets": {}, "links": [], "foreign_keys": []}
    stem = topic.stem
    con = source._connect()
    try:
        result: dict = {"assets": {}, "links": [], "foreign_keys": []}

        # T's OUTGOING FKs (T is the FK source) → related links + ugs:foreign_keys on T's data asset.
        out = _pg(con, f"SELECT relationships::text FROM raw.schema_registry "
                       f"WHERE domain_topic = {_q(stem)} LIMIT 1")
        outgoing = json.loads(out[0][0]) if out and out[0][0] else []
        # Resolve every target's schema up front — the link path needs it, and one IN-list read
        # beats a lookup per relationship.
        schemas = _target_schemas(con, [t for r in outgoing
                                        if (t := r.get("targetDomainTopic"))])
        for rel in outgoing:
            fk = _foreign_key(rel)
            if fk:
                result["foreign_keys"].append(fk)
            target = rel.get("targetDomainTopic")
            if not target:
                continue
            if target not in schemas:
                print(f"[{topic.fqn}] related '{target}': no target_schema in the registry — "
                      "link skipped (item path is per-schema)")
                continue
            result["links"].append(_related_link(target, schemas[target]))

        # T's INCOMING FKs — children whose relationships reference T (jsonb containment).
        contains = '[{"targetDomainTopic": ' + json.dumps(stem) + "}]"
        kids = _pg(con,
                   "SELECT domain_topic, target_schema, display_name, "
                   "business_schema::text, relationships::text FROM raw.schema_registry "
                   f"WHERE relationships @> {_q(contains)}::jsonb")
        for child_topic, tgt_schema, display, bs_text, rel_text in kids:
            child_rels = json.loads(rel_text) if rel_text else []
            rel = next((r for r in child_rels if r.get("targetDomainTopic") == stem), None)
            if rel is None:
                continue
            business_schema = json.loads(bs_text) if bs_text else {}
            if _has_geometry(business_schema):
                # Spatial child has its own STAC item — link to it; its own asset carries the FK.
                # `target_schema` here is the child's own serving schema (same column the
                # materialise path reads), which is exactly the child item's collection.
                if not tgt_schema:
                    print(f"[{topic.fqn}] related '{child_topic}': no target_schema — link skipped")
                    continue
                result["links"].append(_related_link(child_topic, tgt_schema, display))
                continue
            asset = _materialize_child(con, child_topic, tgt_schema, display, rel,
                                       business_schema, stem)
            if asset:
                result["assets"][child_topic] = asset
        return result
    except Exception as e:  # noqa: BLE001 — relationships never block the parent ingest
        print(f"[{topic.fqn}] relationships SKIP: {e}")
        return empty
    finally:
        con.close()
