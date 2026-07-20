"""Identity for the raster producer — the COG/STAC analog of pubs `series_id` and the
vector `Topic`. Naming + paths per docs/RASTER_SPEC.md §5–6.

A raster is either a **1-off** snapshot (`slope`) or a **time-series** slice
(`soil_water` @ a datetime). 1-offs land in the shared `ugs-rasters` collection;
each time-series model gets its own `ugs-raster-<layer>` collection of dated items.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Raster *data* artifacts (the STAC catalog itself lives under core.config.STAC_PREFIX).
# `cog/` matches the other artifact-type sinks (pmtiles/, geoparquet/, thumbs/, stac/) and the path
# ugs-ingest #169 writes for Track A rasters. Layout is unchanged: <prefix>/<layer>/<item_id>.*
COG_PREFIX = os.environ.get("WAREHOUSE_RASTER_COG_PREFIX", "cog")

# Shared collection for one-off snapshots; time-series get a per-model collection.
RASTERS_COLLECTION = "ugs-rasters"


def collection_for(layer: str, *, time_series: bool) -> str:
    return f"ugs-raster-{layer}" if time_series else RASTERS_COLLECTION


def _compact_dt(datetime_iso: str) -> str:
    """ISO 8601 -> compact id stamp: 2026-06-01T00:00:00Z -> 20260601T000000."""
    return re.sub(r"[-:]", "", datetime_iso).split("+")[0].split(".")[0].rstrip("Z")


@dataclass(frozen=True)
class Raster:
    layer: str                       # model / layer name, e.g. "soil_water" or "slope"
    datetime_iso: str | None = None  # set => time-series slice; None => 1-off snapshot

    @property
    def time_series(self) -> bool:
        return self.datetime_iso is not None

    @property
    def collection(self) -> str:
        return collection_for(self.layer, time_series=self.time_series)

    @property
    def item_id(self) -> str:
        return f"{self.layer}_{_compact_dt(self.datetime_iso)}" if self.datetime_iso else self.layer

    @property
    def cog_object_path(self) -> str:
        """`cog/<layer>/<id>.cog.tif`."""
        return f"{COG_PREFIX}/{self.layer}/{self.item_id}.cog.tif"

    @property
    def thumb_object_path(self) -> str:
        return f"{COG_PREFIX}/{self.layer}/{self.item_id}.thumb.png"
