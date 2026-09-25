"""Content fingerprint for skip-unchanged ingest.

A stable, order-independent hash of a topic's transformed view folded with the tiling inputs,
stored on the published STAC item as `ugs:content_hash`. On a later ingest, if the freshly-computed
fingerprint matches the published item AND the PMTiles output still exists, the topic's DATA sinks
(tippecanoe / GeoParquet / DuckLake) are skipped — the rebuild would be byte-identical, so the
expensive tile run is wasted work.

The STAC sink is deliberately NOT gated on this (#54). Since `cff10c9` the item also carries
curated `raw.schema_registry` metadata, which changes without any row changing — a fingerprint
match no longer means "nothing downstream would change". The hash covers the DATA, so it gates
the data sinks and nothing else.

Cost: one extra scan of the already-materialized view (cheap) vs a full tippecanoe run (not).
"""
from __future__ import annotations

import json

import duckdb

from ..core import config, gcs, stac
from . import introspect, sink_pmtiles, sink_stac
from .topics import Topic

CONTENT_HASH_PROP = stac.CONTENT_HASH_PROP


def compute(con: duckdb.DuckDBPyConnection, view: str) -> str:
    """Order-independent content hash of the transformed view, folded with the tiling signature.

    `sum(hash(row))` is commutative, so it does not depend on row order (the view is hilbert-sorted,
    but we deliberately don't lean on that). The row count is carried alongside as a cheap guard so
    that hash cancellations can't masquerade as 'unchanged'."""
    n, h = con.execute(
        f"SELECT count(*)::BIGINT, COALESCE(sum(hash(v)::HUGEINT), 0)::VARCHAR FROM {view} v"
    ).fetchone()
    return f"{n}:{h}:{sink_pmtiles.tiling_signature(introspect.has_ugs_key(con, view))}"


def published_hash(topic: Topic) -> str | None:
    """`ugs:content_hash` on the currently-published STAC item, or None when there is no published
    item / no stored hash / it's unreadable — all of which mean 'treat as changed, rebuild'."""
    path = stac.item_object_path(sink_stac.collection_path(topic.schema), topic.stem)
    try:
        item = json.loads(gcs.get_bytes(path))
    except Exception:  # noqa: BLE001 — no/unreadable item → rebuild
        return None
    return (item.get("properties") or {}).get(CONTENT_HASH_PROP)


def pmtiles_present(topic: Topic) -> bool:
    """The topic's PMTiles object exists in GCS — so a skip can't leave a published item pointing at
    a missing tileset."""
    return gcs.exists(f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles")


def is_unchanged(topic: Topic, fingerprint: str) -> bool:
    """Unchanged ⇔ the fingerprint matches the published item AND the PMTiles output still exists."""
    return fingerprint == published_hash(topic) and pmtiles_present(topic)
