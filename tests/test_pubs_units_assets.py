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


def _item(tier, assets=None):
    path = gm.stac.item_object_path(gm.COLLECTION, f"geologic-maps-{tier}")
    gcs.put_bytes(json.dumps({"id": f"geologic-maps-{tier}", "assets": assets or {"tiles": {"href": "t"}},
                              "stac_extensions": []}).encode(), path, content_type="application/geo+json")
    return path


def test_a_failed_item_patch_leaves_no_state_and_the_next_run_rebuilds_and_patches(run, tmp_path, monkeypatch):  # noqa: F811
    _own(tmp_path, "A-1", ["Qal"], -112.0)
    run.members = {"100k": ["A-1"]}
    run.sums = {"A-1": "sumA"}
    path = _item("100k")
    real = gm.stac.patch_published
    monkeypatch.setattr(gm.stac, "patch_published",
                        lambda *a, **k: (_ for _ in ()).throw(gcs.Changed("raced")))

    result = identify.build_changed()

    assert result.built == [] and len(result.errors) == 1
    assert identify.read_state("100k") is None  # not taken for done
    monkeypatch.setattr(gm.stac, "patch_published", real)
    assert identify.build_changed().built == ["100k"]
    assert "units" in json.loads(gcs.get_bytes(path))["assets"]


def test_an_unchanged_tier_whose_item_lacks_the_asset_is_patched(run, tmp_path):  # noqa: F811
    _own(tmp_path, "A-1", ["Qal"], -112.0)
    run.members = {"100k": ["A-1"]}
    run.sums = {"A-1": "sumA"}
    assert identify.build_changed().built == ["100k"]  # no Item yet: state written, nothing patched
    path = _item("100k")

    assert identify.build_changed().unchanged == ["100k"]

    assert json.loads(gcs.get_bytes(path))["assets"]["units"]["title"] == identify.UNITS_TITLE


def test_patch_published_can_target_an_items_assets():
    path = _item("500k")
    asset = {"href": "u", "file:size": 3}

    assert gm.stac.patch_published(path, {"units": asset}, target="assets")

    item = json.loads(gcs.get_bytes(path))
    assert item["assets"] == {"tiles": {"href": "t"}, "units": asset}
    assert gm.stac.FILE_EXT in item["stac_extensions"]
    assert not gm.stac.patch_published(path + ".none", {"units": asset}, target="assets")


def _put_in(bucket, path, data):
    import obstore

    obstore.put(gcs._store(bucket), path, data)


def test_the_mosaic_asset_reads_the_source_bucket_and_a_review_bake_links_by_the_public_site(monkeypatch):
    state = json.dumps({"pmtiles": {"bytes": 9, "checksum": CHECKSUM}}).encode()
    _put_in("src-bucket", identify.state_object("100k"), state)
    assert identify.mosaic_asset("100k") is None and identify.read_state("100k") is None  # BUCKET has none

    monkeypatch.setattr(identify.config, "SOURCE_BUCKET", "src-bucket")
    monkeypatch.setattr(identify.config, "PUBLIC_CATALOG_URL", "https://maps.example.org/stac/catalog.json")
    asset = identify.mosaic_asset("100k")

    assert asset["href"] == f"https://maps.example.org/{identify.tiles_object('100k')}"
    assert identify.read_state("100k") is None  # the vectors path still reads BUCKET

    monkeypatch.setattr(identify.config, "BUCKET", "src-bucket")  # one bucket: this build's own CDN
    assert identify.mosaic_asset("100k")["href"] == identify.config.public_url(identify.tiles_object("100k"))


def _cogs(monkeypatch, types):
    """The COGs on the bucket, and `_band_types` counting the headers it reads."""
    for sid in types:
        gcs.put_bytes(b"cog", f"{gm.identity.COG_PREFIX}/{sid}.cog.tif", content_type="image/tiff")
    reads: list[list[str]] = []

    def band_types(sids, env):
        reads.append(list(sids))
        return {s: types[s] for s in sids}

    by_sid = {s: {"series_id": s, "pub_year": "2000"} for s in types}
    monkeypatch.setattr(gm, "_band_types", band_types)
    monkeypatch.setattr(gm, "_group_by_tier", lambda: ({"100k": sorted(types), "24k": []}, by_sid))
    return reads


def test_a_16_bit_cog_is_left_out_of_the_identify_members_as_it_is_of_the_bake(monkeypatch):
    types = {"A-1": ["Byte"] * 4, "B-2": ["Int16"] * 4, "C-3": ["Byte"] * 4}
    _cogs(monkeypatch, types)

    assert identify.membership() == {"100k": ["A-1", "C-3"]}
    by_sid = {s: {} for s in types}
    assert gm.tier_members("100k", sorted(types), by_sid, {}, types) == ["A-1", "C-3"]


def test_band_types_are_read_once_per_cog_until_its_etag_changes(monkeypatch):
    types = {"A-1": ["Byte"] * 4, "B-2": ["Byte"] * 4}
    reads = _cogs(monkeypatch, types)

    identify.membership()
    identify.membership()
    assert reads == [["A-1", "B-2"]]

    gcs.put_bytes(b"cog v2", f"{gm.identity.COG_PREFIX}/B-2.cog.tif", content_type="image/tiff")
    identify.membership()
    assert reads == [["A-1", "B-2"], ["B-2"]]
