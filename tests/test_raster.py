"""Raster identity + STAC mapping (warehouse consumer, docs/RASTER_SPEC.md + ugs-ingest #169)."""
import pytest

from ugs_warehouse.raster import consume, sink_stac, source
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
        "pub_id": "OFR-123", "is_mosaic": False,
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
    assert item["properties"]["ugs:pub_id"] == "OFR-123"
    assert item["properties"]["ugs:is_mosaic"] is False  # kept even when False
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


# ---- fetch/alias layer: raw.raster_catalog row -> contract record (pure; DB fetch runs on deploy) ----

def _raw_row(**kw):
    """A raw SELECT row as source.fetch_record sees it — columns already aliased/cast by the SQL
    (publication_date->datetime, bbox_4326::text->bbox_json, ST_AsGeoJSON->geometry_json)."""
    base = {
        "layer": "slope", "item_id": "slope_ofr123_20260601", "collection": "ugs-rasters/slope",
        "datetime": "2026-06-01T00:00:00Z", "bbox_json": "[-114, 37, -109, 42]",
        "geometry_json": '{"type":"Polygon","coordinates":[[[-114,37],[-109,37],[-109,42],[-114,37]]]}',
        "native_crs": "EPSG:26912", "staged_cog_uri": "gs://stagedrasters/slope/slope_ofr123_20260601.cog.tif",
        "title": "Slope", "description": "d", "data_type": "float32", "units": "percent",
        "ugs_author": "J. Geologist", "ugs_pub_type": "OFR", "pub_id": "OFR-123",
        "is_mosaic": False, "has_thumbnail": True,
    }
    return {**base, **kw}


def test_record_from_row_transforms_types():
    rec = source._record_from_row(_raw_row())
    assert rec["bbox"] == [-114, 37, -109, 42]          # jsonb text -> list
    assert rec["geometry"]["type"] == "Polygon"          # ST_AsGeoJSON text -> dict
    assert rec["epsg"] == 26912                          # "EPSG:26912" text -> int
    assert rec["datetime"] == "2026-06-01T00:00:00Z"
    assert rec["pub_id"] == "OFR-123" and rec["is_mosaic"] is False
    # The record feeds consume unchanged — the STAC item binds every mapped property.
    item = consume.stac_item_from_record(rec)
    assert item["properties"]["proj:code"] == "EPSG:26912"
    assert item["properties"]["ugs:pub_id"] == "OFR-123"


def test_record_from_row_null_footprint_and_crs():
    rec = source._record_from_row(_raw_row(geometry_json=None, native_crs=None))
    assert rec["geometry"] is None                       # null footprint -> null geometry (bbox fallback)
    assert rec["epsg"] is None                           # unparseable/absent CRS -> no projection ext


def test_epsg_from_crs_parsing():
    assert source._epsg_from_crs("EPSG:26912") == 26912
    assert source._epsg_from_crs("26912") == 26912
    assert source._epsg_from_crs(None) is None
    assert source._epsg_from_crs("") is None


def test_fetch_record_rejects_malformed_item_id():
    # Guards the (non-parameterizable) postgres_query SQL against injection.
    for bad in ("", "a'; DROP TABLE x;--", "UPPER", "has space", "semi;colon"):
        with pytest.raises(ValueError):
            source.fetch_record(bad)
