"""pubs.geoparquet.write: GeoParquet 1.1 with a bbox covering, CRS and Z kept."""
from __future__ import annotations

import json

import pytest

gpd = pytest.importorskip("geopandas")
pq = pytest.importorskip("pyarrow.parquet")
pytest.importorskip("geoparquet_io")
geom = pytest.importorskip("shapely.geometry")

from ugs_warehouse.pubs import geoparquet  # noqa: E402

COVERING = {"bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}}


def _geo(path):
    return json.loads(pq.read_metadata(path).metadata[b"geo"])


def test_write_geodataframe_is_geoparquet_1_1_with_bbox_covering(tmp_path):
    gdf = gpd.GeoDataFrame({"unit": ["a", "b"]},
                           geometry=[geom.Point(-111, 40), geom.Point(-112, 41)], crs=4326)
    out = tmp_path / "o.parquet"
    geoparquet.write(gdf, out)

    meta = _geo(out)
    assert meta["version"] == "1.1.0"
    assert meta["columns"]["geometry"]["covering"] == COVERING
    back = gpd.read_parquet(out)
    assert sorted(back["unit"]) == ["a", "b"]
    assert back.crs.to_string() == "OGC:CRS84"  # gpio omits crs for 4326, the spec default


def test_write_keeps_projected_crs_and_z(tmp_path):
    poly = geom.Polygon([(0, 0, 1), (10, 0, 2), (10, 10, 3)])
    gdf = gpd.GeoDataFrame({"k": [1]}, geometry=[poly], crs=26912)
    out = tmp_path / "z.parquet"
    geoparquet.write(gdf, out)

    back = gpd.read_parquet(out)
    assert back.crs.to_epsg() == 26912
    assert back.geometry.has_z.all()


def test_write_converts_a_shapefile_path(tmp_path):
    shp = tmp_path / "units.shp"
    gpd.GeoDataFrame({"unit": ["Qa"]}, geometry=[geom.box(0, 0, 1, 1)], crs=26912).to_file(shp)
    out = tmp_path / "units.parquet"
    geoparquet.write(str(shp), out)

    meta = _geo(out)
    assert meta["version"] == "1.1.0"
    assert meta["primary_column"] == "geom"
    assert meta["columns"]["geom"]["covering"] == COVERING
    assert "geom" in pq.read_schema(out).names
    assert gpd.read_parquet(out).crs.to_epsg() == 26912
