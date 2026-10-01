"""GeoParquet metadata helpers shared by the archive sink and the item mirror."""
from __future__ import annotations

from collections.abc import Iterable

_GEOMETRY_TYPES = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon",
                   "MULTIPOINT": "MultiPoint", "MULTILINESTRING": "MultiLineString",
                   "MULTIPOLYGON": "MultiPolygon", "GEOMETRYCOLLECTION": "GeometryCollection"}


def geometry_types(rows: Iterable[tuple[str | None, bool]]) -> list[str]:
    """GeoParquet `geometry_types` from DuckDB (ST_GeometryType, ST_HasZ) rows."""
    return sorted({_GEOMETRY_TYPES.get(t, t) + (" Z" if z else "") for t, z in rows if t})
