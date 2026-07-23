"""Raster identity + STAC mapping (warehouse consumer, docs/RASTER_SPEC.md + ugs-ingest #169)."""
from ugs_warehouse.raster import consume, sink_stac
from ugs_warehouse.raster.identity import Raster


def _raster(**kw):
    base = dict(layer="slope", item_id="slope_OFR123_20260601",
                collection="ugs-rasters/slope", datetime_iso="2026-06-01T00:00:00Z")
    return Raster(**{**base, **kw})


def _record(**kw):
    base = {
        "layer": "slope", "item_id": "slope_OFR123_20260601",
        "collection": "ugs-rasters/slope", "datetime": "2026-06-01T00:00:00Z",
        "bbox": [-114, 37, -109, 42], "geometry": None,
        "epsg": 3857, "has_thumbnail": True,
        "title": "Slope of the Wasatch", "description": "Percent-slope raster",
        "data_type": "float32", "units": "percent",
        "ugs_author": "J. Geologist", "ugs_pub_type": "OFR",
        "staged_cog_uri": "gs://stagedrasters/slope/slope_OFR123_20260601.cog.tif",
    }
    return {**base, **kw}


def test_identity_paths():
    r = _raster()
    assert r.collection == "ugs-rasters/slope"
    assert r.collection_id == "slope"
    assert r.item_id == "slope_OFR123_20260601"
    assert r.cog_object_path == "cog/slope/slope_OFR123_20260601.cog.tif"
    assert r.thumb_object_path == "cog/slope/slope_OFR123_20260601.thumb.png"


def test_build_item_cog_asset_no_webmap_link():
    item = sink_stac.build_item(_raster(), bbox=[-114, 37, -109, 42], geometry=None, has_thumbnail=True)
    assert item["collection"] == "slope"
    # The COG is advertised by its cloud-optimized ASSET (not a web-map-links `cog` link, which
    # isn't a valid web-map-links rel — see raster.sink_stac). STAC Browser/viewer render the asset.
    assert "cloud-optimized" in item["assets"]["cog"]["type"]
    assert "visual" in item["assets"]["cog"]["roles"]
    assert item["assets"]["thumbnail"]["roles"] == ["thumbnail"]
    assert item["geometry"]["type"] == "Polygon"  # derived from bbox
    assert not any(link["rel"] == "cog" for link in item["links"])
    assert "web-map-links" not in " ".join(item.get("stac_extensions") or [])


def test_stac_item_from_record_binds_columns():
    item = consume.stac_item_from_record(_record(data_type=None))  # null data_type dropped
    assert item["id"] == "slope_OFR123_20260601"
    assert item["collection"] == "slope"
    assert item["properties"]["datetime"] == "2026-06-01T00:00:00Z"
    assert item["properties"]["title"] == "Slope of the Wasatch"
    assert item["properties"]["units"] == "percent"
    # snake_case columns map into the ugs: namespace.
    assert item["properties"]["ugs:author"] == "J. Geologist"
    assert item["properties"]["ugs:pub_type"] == "OFR"
    assert "data_type" not in item["properties"]


def test_stac_item_from_record_drops_null_units():
    item = consume.stac_item_from_record(_record(units=None))
    assert "units" not in item["properties"]


def test_promote_copies_cog_and_thumb_then_writes(monkeypatch):
    copies: list[tuple[str, str]] = []
    monkeypatch.setattr(consume.gcs, "copy_from_uri",
                        lambda src, dest, **kw: copies.append((src, dest)))
    written = {}
    monkeypatch.setattr(consume.sink_stac, "write",
                        lambda r, **kw: written.setdefault("path", r.cog_object_path))
    consume.promote(_record())
    # COG promoted staged -> public cog path; thumbnail sibling derived from the staged COG uri.
    assert ("gs://stagedrasters/slope/slope_OFR123_20260601.cog.tif",
            "cog/slope/slope_OFR123_20260601.cog.tif") in copies
    assert ("gs://stagedrasters/slope/slope_OFR123_20260601.thumb.png",
            "cog/slope/slope_OFR123_20260601.thumb.png") in copies
    assert written["path"] == "cog/slope/slope_OFR123_20260601.cog.tif"


def test_promote_skips_thumb_when_absent(monkeypatch):
    copies: list[str] = []
    monkeypatch.setattr(consume.gcs, "copy_from_uri",
                        lambda src, dest, **kw: copies.append(dest))
    monkeypatch.setattr(consume.sink_stac, "write", lambda r, **kw: "ok")
    consume.promote(_record(has_thumbnail=False))
    assert copies == ["cog/slope/slope_OFR123_20260601.cog.tif"]  # no thumb copy
