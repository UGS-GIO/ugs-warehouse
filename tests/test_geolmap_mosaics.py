from unittest.mock import patch

from ugs_warehouse.pubs import geolmap_mosaics as gm
from ugs_warehouse.pubs import identity


def test_mosaic_stamps_topic_and_links_members_by_real_collection(capsys):
    by_sid = {
        # USGS-authored Utah quad -> main catalog (ugs-publications)
        "GQ-968": {"series_id": "GQ-968", "pub_publisher": "USGS", "pub_name": "Geologic map of Foo"},
        # foreign publisher -> ugs-external (the ALL-5912 404 trap)
        "BYU-1": {"series_id": "BYU-1", "pub_publisher": "BYU", "pub_name": "A thesis map"},
    }
    captured = {}
    with patch.object(gm.stac, "write_item", side_effect=lambda it: captured.setdefault("item", it)):
        # ORPHAN-9 has a COG but no pub record -> must be skipped (no item to link to)
        gm._write_item("24k", ["GQ-968", "BYU-1", "ORPHAN-9"], gm.mosaic_object("24k"), by_sid)
    item = captured["item"]
    assert item["properties"]["ugs:topic"] == "geologic"
    assert item["properties"]["ugs:map_count"] == 3   # count still reflects every stitched COG
    # The mosaic is derived from its member maps -> STAC provenance rel, not generic "related".
    derived = [lnk for lnk in item["links"] if lnk["rel"] == "derived_from"]
    hrefs = [lnk["href"] for lnk in derived]
    assert len(derived) == 2  # ORPHAN-9 skipped
    assert any(h.endswith(f"/{identity.PUBLICATIONS_COLLECTION}/GQ/GQ-968/GQ-968.json") for h in hrefs)
    assert any(h.endswith(f"/{identity.EXTERNAL_COLLECTION}/BYU/BYU-1/BYU-1.json") for h in hrefs)
    assert "ORPHAN-9" in capsys.readouterr().err  # orphan COG must warn to stderr (fail loud)


_PARK_CITY_PUBS = [
    {"series_id": "GQ-852", "pub_scale": "1:24,000", "pub_year": "1971"},
    {"series_id": "OFR-677", "pub_scale": "1:24,000", "pub_year": "2017"},
    {"series_id": "M-296DM", "pub_scale": "1:24,000", "pub_year": "2022"},
]
_PARK_CITY_QMAP = {
    "GQ-852": "Park City East Quad",
    "OFR-677": "Park City East Quad",
    "M-296DM": "Park City East Quad",
}
_PARK_CITY_GRAPH = {
    "GQ-852": {"deprecated": True},
    "OFR-677": {"deprecated": True},
    "M-296DM": {"deprecated": False},
}


def test_group_by_tier_current_drops_deprecated_editions():
    """--editions current (default): only the newest map per (quad, scale) group survives —
    superseded editions of the same quad are dropped before the tier bins ever see them, so the
    VRT never stitches them in."""
    with patch.object(gm, "_cog_sids", return_value=set(_PARK_CITY_QMAP)), \
         patch.object(gm.source, "read_pubs", return_value=_PARK_CITY_PUBS), \
         patch.object(gm.editions, "quad_by_series", return_value=_PARK_CITY_QMAP), \
         patch.object(gm.editions, "edition_graph", return_value=_PARK_CITY_GRAPH) as mock_graph:
        groups, by_sid = gm._group_by_tier(editions="current")

    assert groups["24k"] == ["M-296DM"]
    assert set(by_sid) == {"GQ-852", "OFR-677", "M-296DM"}  # pubs_by_sid still carries every pub
    # the footprints qmap must be loaded ONCE and reused as edition_graph's quad_by_sid (not a
    # second CDN read from inside edition_graph itself).
    mock_graph.assert_called_once()
    assert mock_graph.call_args.kwargs.get("quad_by_sid") == _PARK_CITY_QMAP
    called_sids = {p["series_id"] for p in mock_graph.call_args.args[0]}
    assert called_sids == {"GQ-852", "OFR-677", "M-296DM"}


def test_group_by_tier_all_keeps_every_edition_and_skips_the_graph():
    """--editions all reproduces today's behavior (every COG, every edition) and must not pay for
    a footprints/CDN read at all — no --quads means no reason to touch the network."""
    with patch.object(gm, "_cog_sids", return_value=set(_PARK_CITY_QMAP)), \
         patch.object(gm.source, "read_pubs", return_value=_PARK_CITY_PUBS), \
         patch.object(gm.editions, "quad_by_series") as mock_qmap, \
         patch.object(gm.editions, "edition_graph") as mock_graph:
        groups, _ = gm._group_by_tier(editions="all")

    assert set(groups["24k"]) == {"GQ-852", "OFR-677", "M-296DM"}
    mock_graph.assert_not_called()
    mock_qmap.assert_not_called()


def test_group_by_tier_quads_filter_restricts_membership():
    """--quads restricts the tier bins to members whose footprints quad_name matches (casefolded
    comparison on both sides) — the scoped/demo build path. It composes with --editions all with
    no deprecation graph call at all (editions=current is what triggers that graph)."""
    pubs = [
        {"series_id": "GQ-852", "pub_scale": "1:24,000", "pub_year": "1971"},
        {"series_id": "M-1", "pub_scale": "1:24,000", "pub_year": "1990"},
    ]
    qmap = {"GQ-852": "Park City East Quad", "M-1": "Some Other Quad"}
    with patch.object(gm, "_cog_sids", return_value={"GQ-852", "M-1"}), \
         patch.object(gm.source, "read_pubs", return_value=pubs), \
         patch.object(gm.editions, "quad_by_series", return_value=qmap), \
         patch.object(gm.editions, "edition_graph") as mock_graph:
        groups, _ = gm._group_by_tier(editions="all", quads="park city east quad")

    assert groups["24k"] == ["GQ-852"]  # M-1's quad doesn't match -> excluded
    mock_graph.assert_not_called()  # editions=all -> no deprecation filtering even with --quads


def test_mosaic_object_and_write_item_honor_optional_suffix():
    """The scoped/--quads demo build writes to a `-test` suffixed object + item id so it never
    clobbers the real tier; the default (no suffix) call sites are unaffected."""
    assert gm.mosaic_object("24k") == f"{identity.MOSAIC_PREFIX}/geologic-maps-24k.pmtiles"
    assert gm.mosaic_object("24k", suffix="-test") == (
        f"{identity.MOSAIC_PREFIX}/geologic-maps-24k-test.pmtiles")

    by_sid = {"GQ-968": {"series_id": "GQ-968", "pub_publisher": "USGS", "pub_name": "Foo"}}
    captured = {}
    with patch.object(gm.stac, "write_item", side_effect=lambda it: captured.setdefault("item", it)):
        gm._write_item("24k", ["GQ-968"], gm.mosaic_object("24k", suffix="-test"), by_sid,
                        suffix="-test")
    assert captured["item"]["id"] == "geologic-maps-24k-test"
