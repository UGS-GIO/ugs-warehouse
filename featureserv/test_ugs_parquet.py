"""The plugin against the pinned pygeoapi, on a file shaped like our archives: geometry in `geom`,
a bbox covering column, ids in `feature_id`. Runs in the image build."""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box
from ugs_parquet import GeoParquetProvider

BOX = [0, 0, 10, 10]


@pytest.fixture(scope="module")
def provider_def(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "layer.parquet"
    gdf = gpd.GeoDataFrame(
        {"feature_id": [1, 2, 3], "name": ["inside", "crosses the edge", "outside"]},
        geometry=gpd.GeoSeries([box(1, 1, 2, 2), box(9, 9, 12, 12), box(20, 20, 21, 21)],
                               crs="OGC:CRS84"))
    gdf.rename_geometry("geom").to_parquet(path, write_covering_bbox=True)
    return {"name": "ugs_parquet.GeoParquetProvider", "type": "feature",
            "id_field": "feature_id", "data": {"source": str(path)}}


def names(result):
    return sorted(f["properties"]["name"] for f in result["features"])


def test_geometry_comes_from_the_primary_column(provider_def):
    p = GeoParquetProvider(provider_def)
    features = p.query()["features"]
    assert all(f["geometry"]["type"] == "Polygon" for f in features)
    assert not any({"geom", "geometry", "bbox"} & f["properties"].keys() for f in features)
    assert "geom" not in p.get_fields()
    assert p.get(2)["geometry"]["type"] == "Polygon"


def test_bbox_keeps_features_that_cross_its_edge(provider_def):
    p = GeoParquetProvider(provider_def)
    assert names(p.query(bbox=BOX)) == ["crosses the edge", "inside"]
    assert p.query(bbox=BOX, resulttype="hits")["numberMatched"] == 2
    # The bbox does not carry over to the next query on the same provider.
    assert p.query(resulttype="hits")["numberMatched"] == 3
    assert names(p.query(bbox=[0, 0, -5, 10, 10, 5])) == ["crosses the edge", "inside"]


def test_paging_skip_geometry_and_select_properties(provider_def):
    p = GeoParquetProvider(provider_def)
    assert len(p.query(bbox=BOX, limit=1, offset=1)["features"]) == 1
    assert all(f["geometry"] is None for f in p.query(skip_geometry=True)["features"])
    feature = p.query(select_properties=["name"], limit=1)["features"][0]
    assert feature["geometry"] is not None and "name" in feature["properties"]


def test_metadata_naming_a_missing_geometry_column_fails_clearly(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "broken.parquet"
    geo = {"version": "1.1.0", "primary_column": "geom", "columns": {"geom": {"encoding": "WKB"}}}
    pq.write_table(pa.table({"feature_id": [1]}).replace_schema_metadata({"geo": json.dumps(geo)}),
                   path)
    with pytest.raises(Exception, match="no geometry column"):
        GeoParquetProvider({"name": "x", "type": "feature", "id_field": "feature_id",
                            "data": {"source": str(path)}})


def test_bbox_on_flat_columns_without_a_covering(tmp_path):
    """Our GeoParquet 1.0 archives have bbox_xmin..bbox_ymax columns but no covering metadata."""
    path = tmp_path / "v10.parquet"
    gdf = gpd.GeoDataFrame(
        {"feature_id": [1, 2, 3], "name": ["inside", "crosses the edge", "outside"]},
        geometry=gpd.GeoSeries([box(1, 1, 2, 2), box(9, 9, 12, 12), box(20, 20, 21, 21)],
                               crs="OGC:CRS84"))
    b = gdf.bounds
    gdf = gdf.assign(bbox_xmin=b.minx, bbox_ymin=b.miny, bbox_xmax=b.maxx, bbox_ymax=b.maxy)
    gdf.rename_geometry("geom").to_parquet(path)
    p = GeoParquetProvider({"name": "x", "type": "feature", "id_field": "feature_id",
                            "data": {"source": str(path)}})
    assert names(p.query(bbox=BOX)) == ["crosses the edge", "inside"]
