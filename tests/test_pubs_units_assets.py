"""The `units` asset on mosaic and plate Items (pubs/identify.py, geolmap_mosaics, sink_stac)."""
from __future__ import annotations

import json

import pytest
from test_pubs_identify import CHECKSUM, _own, run  # noqa: F401 (run is a fixture)

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import geolmap_mosaics as gm
from ugs_warehouse.pubs import identify, sink_stac, unit_polys, vectors

pytest.importorskip("geopandas")


def test_the_mosaic_item_gets_the_units_asset_once_the_layer_is_built(run, tmp_path):  # noqa: F811
    by_sid = {"A-1": {"series_id": "A-1", "pub_name": "Map A"}}
    published = gm.PublishedPubs(frozenset(), frozenset())
    captured = []

    def write(sids, bounds=(-112.0, 39.0, -111.5, 39.4)):
        gm.stac.write_item = lambda it: captured.append(it)
        gm._write_item("100k", sids, gm.mosaic_object("100k"), by_sid, bounds=list(bounds),
                       published=published)
        return captured[-1]

    original = gm.stac.write_item
    try:
        assert "units" not in write(["A-1"])["assets"]  # nothing built yet

        _own(tmp_path, "A-1", ["Qal"], -112.0)
        run.members = {"100k": ["A-1"]}
        run.sums = {"A-1": "sumA"}
        identify.build_changed()

        units = write(["A-1"])["assets"]["units"]
    finally:
        gm.stac.write_item = original
    assert units["type"] == "application/vnd.pmtiles" and units["roles"] == ["data"]
    assert units["title"] == "Geologic units (identify)"
    assert units["href"].endswith(f"{identify.identify_prefix()}/units-100k.pmtiles")
    assert units["file:size"] == 5 and units["file:checksum"].startswith("1220")


def test_a_published_mosaic_item_is_patched_in_place_and_an_unpublished_one_is_left(run, tmp_path):  # noqa: F811
    _own(tmp_path, "A-1", ["Qal"], -112.0)
    run.members = {"100k": ["A-1"], "500k": ["A-1"]}
    run.sums = {"A-1": "sumA"}
    path = gm.stac.item_object_path(gm.COLLECTION, "geologic-maps-100k")
    gcs.put_bytes(json.dumps({"id": "geologic-maps-100k", "assets": {"tiles": {"href": "t"}},
                              "stac_extensions": []}).encode(), path, content_type="application/geo+json")

    result = identify.build_changed()

    assert sorted(result.built) == ["100k", "500k"]  # the 500k mosaic has no Item yet: not an error
    item = json.loads(gcs.get_bytes(path))
    assert item["assets"]["tiles"] == {"href": "t"} and item["assets"]["units"]["title"].startswith("Geologic")
    assert gm.stac.FILE_EXT in item["stac_extensions"]


def _plate(unit_polys_record):
    p = {"series_id": "M-1DM", "pub_name": "Map", "pub_publisher": "UGS"}
    _, items = sink_stac.build_publication(p, [], has_cog=True, has_units=True,
                                         unit_polys=unit_polys_record)
    return next(it for it in items if it["id"].endswith("_plate"))


def test_a_plate_with_a_unit_product_points_its_units_asset_at_the_pubs_pmtiles():
    record = {"rows": 4, "bytes": 10, "checksum": CHECKSUM, "pmtiles": {"bytes": 77, "checksum": CHECKSUM}}

    asset = _plate(record)["assets"]["units"]

    assert asset["href"].endswith(vectors.unit_polys_pmtiles_object("M-1DM"))
    assert asset["type"] == "application/vnd.pmtiles" and asset["roles"] == ["data"]
    assert asset["title"] == "Geologic units (identify)"
    assert asset["file:size"] == 77 and asset["file:checksum"] == CHECKSUM


def test_a_plate_without_unit_data_has_no_units_asset():
    assert "units" not in _plate(None)["assets"]
    assert unit_polys.TILE_LAYER == "units"
