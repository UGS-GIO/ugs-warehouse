"""pubs/vectors.py: 3D layers survive the GeoPackage build with Z intact."""
from __future__ import annotations

import pytest

from ugs_warehouse.pubs import vectors

gpd = pytest.importorskip("geopandas")
shapely = pytest.importorskip("shapely")
from shapely import force_3d, get_coordinates  # noqa: E402
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, box  # noqa: E402

Z = 7.5


def _frame(kind):
    geoms = {
        "Point Z": [Point(1, 2), Point(3, 4)],
        "MultiLineString Z": [MultiLineString([LineString([(0, 0), (1, 1)])]),
                              MultiLineString([LineString([(2, 2), (3, 3)])])],
        "MultiPolygon Z": [MultiPolygon([box(0, 0, 1, 1)]), MultiPolygon([box(2, 2, 3, 3)])],
    }[kind]
    return gpd.GeoDataFrame({"n": [1, 2]}, geometry=[force_3d(g, Z) for g in geoms], crs="EPSG:4326")


def _zs(geom):
    return get_coordinates(geom, include_z=True)[:, 2]


@pytest.mark.parametrize("kind", ["Point Z", "MultiLineString Z", "MultiPolygon Z"])
def test_3d_layers_are_written_with_z_intact(kind, tmp_path):
    src = str(tmp_path / "l.parquet")
    vectors.geoparquet.write(_frame(kind), src)
    out = str(tmp_path / "out.gpkg")

    vectors._append_layer(src, out, {"label": "l", "spatial": True})

    back = gpd.read_file(out, layer="l")
    assert len(back) == 2 and back.geometry.has_z.all()
    assert {g.geom_type for g in back.geometry} == {kind.removesuffix(" Z")}
    assert all(z == Z for g in back.geometry for z in _zs(g))


def test_a_mixed_2d_and_3d_layer_keeps_z_where_it_exists(tmp_path):
    frame = gpd.GeoDataFrame(
        {"n": [1, 2]}, geometry=[force_3d(box(0, 0, 1, 1), Z), box(2, 2, 3, 3)], crs="EPSG:4326")
    src = str(tmp_path / "m.parquet")
    vectors.geoparquet.write(frame, src)
    out = str(tmp_path / "out.gpkg")

    vectors._append_layer(src, out, {"label": "m", "spatial": True})

    back = gpd.read_file(out, layer="m")
    assert set(_zs(back.geometry.iloc[0])) == {Z}
