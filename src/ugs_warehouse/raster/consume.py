"""Warehouse-side raster consumer — Track A of docs/RASTER_SPEC.md.

ugs-ingest (PR #169) writes validated rasters to `raw.raster_catalog`; dataELT flips
them dev→prod; the warehouse then (1) promotes the staged COG to the public bucket and
(2) emits a STAC item into the unified catalog. This module owns step 2 (certain) and
sketches step 1 (blocked on #169).

PROVISIONAL: the `raw.raster_catalog` column names below are a placeholder — bind them to
the real schema once #169 merges. The STAC mapping itself is stable and tested.
"""
from __future__ import annotations

from . import sink_stac
from .identity import Raster

# Property keys we surface from a catalog record onto the STAC item (provisional names).
_PROP_KEYS = ("title", "description", "ugs:author", "ugs:pub_type", "data_type", "units")


def raster_from_record(record: dict) -> Raster:
    """raw.raster_catalog row -> Raster identity. `datetime` present => time-series slice."""
    return Raster(layer=record["layer"], datetime_iso=record.get("datetime"))


def stac_item_from_record(record: dict) -> dict:
    """Build (don't upload) the STAC item for a catalog record — pure, testable."""
    props = {k: record[k] for k in _PROP_KEYS if record.get(k) not in (None, "")}
    return sink_stac.build_item(
        raster_from_record(record),
        bbox=record["bbox"],
        geometry=record.get("geometry"),
        properties=props,
        has_thumbnail=bool(record.get("has_thumbnail")),
    )


def promote(record: dict) -> str:
    """Full consumer: promote staged COG -> public bucket, then write the STAC item.

    Step 1 (COG promote) is blocked on #169: the staged COG lives in a *separate* bucket
    (`gs://...stagedrasters`), so it needs cross-bucket read access that `core.gcs` (bound
    to the public bucket) does not yet have. Wire it when #169 lands — likely a streamed
    obstore copy `staged_uri -> raster.cog_object_path` to avoid buffering large COGs.
    """
    raise NotImplementedError(
        "COG promote pending ugs-ingest #169 (raw.raster_catalog contract + staged-bucket access); "
        "STAC emit is ready via stac_item_from_record() + core.stac.write_item/refresh_catalog",
    )
