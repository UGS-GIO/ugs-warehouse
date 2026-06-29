"""Raster identity + STAC mapping (warehouse consumer, docs/RASTER_SPEC.md)."""
from ugs_warehouse.raster import consume, sink_stac
from ugs_warehouse.raster.identity import RASTERS_COLLECTION, Raster


def test_oneoff_identity():
    r = Raster(layer="slope")
    assert not r.time_series
    assert r.collection == RASTERS_COLLECTION
    assert r.item_id == "slope"
    assert r.cog_object_path == "raster/cogs/slope/slope.cog.tif"


def test_timeseries_identity():
    r = Raster(layer="soil_water", datetime_iso="2026-06-01T00:00:00Z")
    assert r.time_series
    assert r.collection == "ugs-raster-soil_water"
    assert r.item_id == "soil_water_20260601T000000"
    assert r.cog_object_path == "raster/cogs/soil_water/soil_water_20260601T000000.cog.tif"


def test_build_item_cog_asset_no_webmap_link():
    item = sink_stac.build_item(Raster(layer="slope"), bbox=[-114, 37, -109, 42], geometry=None, has_thumbnail=True)
    assert item["collection"] == RASTERS_COLLECTION
    # The COG is advertised by its cloud-optimized ASSET (not a web-map-links `cog` link, which
    # isn't a valid web-map-links rel — see raster.sink_stac). STAC Browser/viewer render the asset.
    assert "cloud-optimized" in item["assets"]["cog"]["type"]
    assert "visual" in item["assets"]["cog"]["roles"]
    assert item["assets"]["thumbnail"]["roles"] == ["thumbnail"]
    assert item["geometry"]["type"] == "Polygon"  # derived from bbox
    assert not any(link["rel"] == "cog" for link in item["links"])
    assert "web-map-links" not in " ".join(item.get("stac_extensions") or [])


def test_stac_item_from_record_filters_props():
    record = {
        "layer": "soil_water", "datetime": "2026-06-01T00:00:00Z",
        "bbox": [-114, 37, -109, 42], "title": "Soil Water", "units": "mm",
        "data_type": None,  # dropped (None)
    }
    item = consume.stac_item_from_record(record)
    assert item["id"] == "soil_water_20260601T000000"
    assert item["properties"]["title"] == "Soil Water"
    assert item["properties"]["units"] == "mm"
    assert "data_type" not in item["properties"]
    assert item["properties"]["datetime"] == "2026-06-01T00:00:00Z"
