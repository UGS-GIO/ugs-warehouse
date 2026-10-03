"""pygeoapi's Parquet provider, fixed for GeoParquet 1.1 files like ours.

The upstream provider (0.24) has two gaps:
- it reads geometry only from a column named `geometry`; ours is `geom` (the `primary_column`)
- its bbox filter keeps features inside the box, where OGC API Features wants every feature that
  intersects it, so features crossing the edge of a client's view go missing

pygeoapi builds a provider per request, so per-request state on the instance is safe.
"""
from __future__ import annotations

import json

import pyarrow.compute as pc
import pyarrow.dataset
from pygeoapi.provider.base import ProviderQueryError
from pygeoapi.provider.parquet import ParquetProvider


class _RenamedGeometry:
    """The dataset with its primary geometry column read as `geometry`, and an extra filter."""

    def __init__(self, ds: pyarrow.dataset.Dataset, column: str):
        self.source, self._column, self.extra = ds, column, None
        i = ds.schema.get_field_index(column)
        self.schema = ds.schema.set(i, ds.schema.field(i).with_name("geometry"))

    def filtered(self, filter_):
        return self.extra if filter_ is None else filter_ & self.extra if self.extra is not None \
            else filter_

    def scanner(self, columns=None, filter=None, **kwargs):  # noqa: A002 (pyarrow's name)
        names = self.schema.names if columns is None else columns
        exprs = {n: pc.field(self._column if n == "geometry" else n) for n in names}
        return self.source.scanner(columns=exprs, filter=self.filtered(filter), **kwargs)


class GeoParquetProvider(ParquetProvider):
    def __init__(self, provider_def):
        super().__init__(provider_def)
        geo = json.loads((self.ds.schema.metadata or {}).get(b"geo", b"{}"))
        column = geo.get("primary_column") or "geometry"
        self._covering = ((geo.get("columns") or {}).get(column) or {}).get("covering", {}).get("bbox")
        if column not in self.ds.schema.names:
            raise ProviderQueryError(f"{self.source} has no geometry column {column!r}")
        self.ds = _RenamedGeometry(self.ds, column)
        self._fields = {}
        self.get_fields()

    def query(self, *args, bbox=[], **kwargs):  # noqa: B006 (the base signature)
        self.ds.extra = None
        if bbox:
            if not self._covering:
                raise ProviderQueryError("Dataset has no GeoParquet bbox covering to filter on")
            if len(bbox) == 6:  # minx, miny, minz, maxx, maxy, maxz
                bbox = [*bbox[:2], *bbox[3:5]]
            minx, miny, maxx, maxy = (float(b) for b in bbox)
            c = {k: pc.field(*path) for k, path in self._covering.items()}
            self.ds.extra = ((c["xmax"] >= minx) & (c["xmin"] <= maxx)
                             & (c["ymax"] >= miny) & (c["ymin"] <= maxy))
        return super().query(*args, bbox=[], **kwargs)

    def _response_feature_hits(self, filter):
        # Counting needs a real Dataset; the column rename never matters to a count.
        ds, self.ds = self.ds, self.ds.source
        try:
            return super()._response_feature_hits(ds.filtered(filter))
        finally:
            self.ds = ds
