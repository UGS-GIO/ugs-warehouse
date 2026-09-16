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
        groups, by_sid = gm._group_by_tier(edition_mode="current")

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
        groups, _ = gm._group_by_tier(edition_mode="all")

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
        groups, _ = gm._group_by_tier(edition_mode="all", quads="park city east quad")

    assert groups["24k"] == ["GQ-852"]  # M-1's quad doesn't match -> excluded
    mock_graph.assert_not_called()  # editions=all -> no deprecation filtering even with --quads


def test_mosaic_object_honors_optional_suffix_and_build_wires_write_stac_item():
    """`mosaic_object` still takes an optional `-test-{edition_mode}` suffix (e.g. `-test-current`/
    `-test-all`) routing the scoped/--quads demo build to its own scratch object, never the real
    tier. But that scoped build must write NO STAC item (ALL-5954 final review must-fix:
    `_write_item` no longer takes a suffix at all — an uncataloged scoped item would otherwise leak
    into the live collection the next time a real run's `refresh_catalog()` lists GCS). `build()`
    wires `write_stac_item=False` into `build_tier` for a scoped run and `write_stac_item=True`
    (the default) for a real one, and only refreshes the catalog on the real (non-scoped) path."""
    assert gm.mosaic_object("24k") == f"{identity.MOSAIC_PREFIX}/geologic-maps-24k.pmtiles"
    assert gm.mosaic_object("24k", suffix="-test-current") == (
        f"{identity.MOSAIC_PREFIX}/geologic-maps-24k-test-current.pmtiles")

    by_sid = {"M-296DM": {"series_id": "M-296DM", "pub_publisher": "USGS", "pub_name": "Foo"}}

    # scoped (--quads) build: build_tier gets the scoped suffix AND write_stac_item=False, and the
    # catalog is never refreshed.
    with patch.object(gm, "_group_by_tier", return_value=({"24k": ["M-296DM"]}, by_sid)), \
         patch.object(gm, "build_tier", return_value=True) as mock_build_tier, \
         patch.object(gm.stac, "refresh_catalog") as mock_refresh:
        built = gm.build(["24k"], quads="Park City East Quad", edition_mode="current")
    assert built == 1
    mock_build_tier.assert_called_once()
    assert mock_build_tier.call_args.kwargs.get("suffix") == "-test-current"
    assert mock_build_tier.call_args.kwargs.get("write_stac_item") is False
    mock_refresh.assert_not_called()

    # non-scoped (real) build: no suffix, write_stac_item=True, catalog IS refreshed.
    with patch.object(gm, "_group_by_tier", return_value=({"24k": ["M-296DM"]}, by_sid)), \
         patch.object(gm, "build_tier", return_value=True) as mock_build_tier2, \
         patch.object(gm.stac, "refresh_catalog") as mock_refresh2:
        built2 = gm.build(["24k"])
    assert built2 == 1
    assert mock_build_tier2.call_args.kwargs.get("suffix") == ""
    assert mock_build_tier2.call_args.kwargs.get("write_stac_item") is True
    mock_refresh2.assert_called_once()


def test_build_composes_deprecation_and_quads_filters_with_edition_scoped_suffix():
    """The actual demo path: `edition_mode="current"` WITH `--quads` together, through the real
    `build()` (GDAL/GCS/STAC-write all mocked out via `build_tier`). Both filters must compose —
    a deprecated sid is dropped, a sid outside the requested quad is dropped, a current in-quad
    sid survives — and the scoped build must route to the edition-scoped `-test-current` suffix."""
    pubs = [
        # current + in the requested quad -> must survive both filters
        {"series_id": "M-296DM", "pub_scale": "1:24,000", "pub_year": "2022"},
        # deprecated + in the requested quad -> dropped by the edition filter
        {"series_id": "GQ-852", "pub_scale": "1:24,000", "pub_year": "1971"},
        # current, but a different quad -> dropped by the --quads filter
        {"series_id": "M-1", "pub_scale": "1:24,000", "pub_year": "1990"},
    ]
    qmap = {
        "M-296DM": "Park City East Quad",
        "GQ-852": "Park City East Quad",
        "M-1": "Some Other Quad",
    }
    # M-1 is absent from the graph entirely (not a quad-edition member anywhere) -- must NOT be
    # treated as deprecated just because it's missing; only the --quads filter should drop it.
    graph = {"M-296DM": {"deprecated": False}, "GQ-852": {"deprecated": True}}
    with patch.object(gm, "_cog_sids", return_value=set(qmap)), \
         patch.object(gm.source, "read_pubs", return_value=pubs), \
         patch.object(gm.editions, "quad_by_series", return_value=qmap), \
         patch.object(gm.editions, "edition_graph", return_value=graph), \
         patch.object(gm, "build_tier", return_value=True) as mock_build_tier:
        built = gm.build(["24k"], edition_mode="current", quads="Park City East Quad")

    assert built == 1
    mock_build_tier.assert_called_once()
    call = mock_build_tier.call_args
    assert call.args[0] == "24k"
    assert call.args[1] == ["M-296DM"]   # deprecated GQ-852 AND out-of-quad M-1 both dropped
    assert call.kwargs.get("suffix") == "-test-current"
