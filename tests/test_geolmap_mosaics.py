import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

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
    assert call.kwargs.get("write_stac_item") is False  # scoped build writes NO cataloged item (Defect 2)


def test_build_non_scoped_writes_catalog_item_and_refreshes():
    """A normal (no --quads) build writes the real tier: empty suffix, DOES write the STAC item
    (write_stac_item=True), and refreshes the catalog. Guards the Defect-2 fix against flipping the
    default and silently stopping real mosaics from being cataloged/refreshed."""
    pubs = [{"series_id": "M-296DM", "pub_scale": "1:24,000", "pub_year": "2022"}]
    with patch.object(gm, "_cog_sids", return_value={"M-296DM"}), \
         patch.object(gm.source, "read_pubs", return_value=pubs), \
         patch.object(gm.editions, "quad_by_series", return_value={"M-296DM": "Park City East Quad"}), \
         patch.object(gm.editions, "edition_graph", return_value={"M-296DM": {"deprecated": False}}), \
         patch.object(gm, "build_tier", return_value=True) as mock_build_tier, \
         patch.object(gm.stac, "refresh_catalog") as mock_refresh:
        built = gm.build(["24k"], edition_mode="current")

    assert built == 1
    call = mock_build_tier.call_args
    assert call.kwargs.get("suffix") == ""
    assert call.kwargs.get("write_stac_item") is True
    mock_refresh.assert_called_once()  # real builds refresh the catalog; scoped --quads builds don't


def test_vrt_order_sorts_oldest_to_newest_so_the_newest_draws_on_top():
    """ALL-5954 Clinton review, FIX 2: gdalbuildvrt gives the LAST source priority on overlap, so
    the VRT input list must be ordered oldest->newest (newest last) — otherwise which edition draws
    on top of an overlap is arbitrary (today: whatever order `sids` arrives in)."""
    by_sid = {
        "M-1": {"pub_year": "2022"},
        "M-2": {"pub_year": "1971"},
        "M-3": {"pub_year": "1990"},
    }
    assert gm._vrt_order(["M-1", "M-2", "M-3"], by_sid) == ["M-2", "M-3", "M-1"]


def test_vrt_order_puts_unparseable_years_at_the_bottom_and_breaks_ties_by_sid():
    by_sid = {
        "M-1": {"pub_year": "2022"},
        "M-2": {"pub_year": ""},       # blank -> 0 -> bottom
        "M-3": {"pub_year": "n/a"},    # unparseable -> 0 -> bottom
        "M-4": {"pub_year": "2022"},   # ties with M-1 -> broken by sid
    }
    assert gm._vrt_order(["M-1", "M-2", "M-3", "M-4"], by_sid) == ["M-2", "M-3", "M-1", "M-4"]


def test_pack_tiles_to_mbtiles_flips_y_verbatim_and_skips_sidecars(tmp_path):
    """The packer copies `gdal raster tile`'s XYZ tree into MBTiles byte-for-byte (no re-encode) and
    flips y to TMS. Non-tile sidecars (.aux.xml) are skipped; metadata carries format/zoom/bounds so
    `pmtiles convert` can read it."""
    tiles = {(14, 3, 6): b"webp-A", (14, 3, 7): b"webp-B", (13, 1, 2): b"webp-C"}
    for (z, x, y), blob in tiles.items():
        d = tmp_path / "t" / str(z) / str(x)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{y}.webp").write_bytes(blob)
    (tmp_path / "t" / "14" / "3" / "6.webp.aux.xml").write_text("<PAMDataset/>")   # sidecar, must be skipped

    mb = tmp_path / "out.mbtiles"
    n, minz, maxz = gm._pack_tiles_to_mbtiles(
        str(tmp_path / "t"), str(mb), bounds=[-114.0, 37.0, -109.0, 42.0], name="geologic-maps-24k")
    assert (n, minz, maxz) == (3, 13, 14)

    con = sqlite3.connect(str(mb))
    rows = {(z, x, y): blob for z, x, y, blob in
            con.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")}
    meta = dict(con.execute("SELECT name, value FROM metadata"))
    con.close()
    # y flipped XYZ->TMS: (14,3,6) -> row 2^14-1-6, blob byte-identical
    assert rows[(14, 3, (1 << 14) - 1 - 6)] == b"webp-A"
    assert rows[(14, 3, (1 << 14) - 1 - 7)] == b"webp-B"
    assert rows[(13, 1, (1 << 13) - 1 - 2)] == b"webp-C"
    assert meta["format"] == "webp"
    assert meta["minzoom"] == "13" and meta["maxzoom"] == "14"
    assert meta["bounds"] == "-114.0,37.0,-109.0,42.0"
    con = sqlite3.connect(str(mb))
    with pytest.raises(sqlite3.IntegrityError):             # MBTiles spec: metadata.name is unique
        con.execute("INSERT INTO metadata VALUES ('format', 'png')")
    con.close()


def test_pack_tiles_to_mbtiles_raises_on_empty_tree(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError):
        gm._pack_tiles_to_mbtiles(str(tmp_path / "empty"), str(tmp_path / "x.mbtiles"),
                                  bounds=[-1, -1, 1, 1], name="x")


def test_vrt_zoom_and_bounds_reads_zoom_and_real_extent():
    """_vrt_zoom_and_bounds derives the native web-mercator zoom from the finest pixel size AND the
    real [W,S,E,N] footprint from wgs84Extent, both from one gdalinfo -json. A resolution ~2% off
    z17 still rounds to z17, but it is not on z17's grid."""
    xres = (gm._WEBMERC_Z0_MPP / (2 ** 17)) * 1.02
    stdout = json.dumps({
        "geoTransform": [0, xres, 0, 0, 0, -xres],
        "wgs84Extent": {"type": "Polygon", "coordinates": [[
            [-112.0, 39.0], [-111.5, 39.0], [-111.5, 39.4], [-112.0, 39.4], [-112.0, 39.0]]]},
    })
    with patch.object(gm.subprocess, "run", return_value=MagicMock(stdout=stdout)):
        zoom, aligned, bounds = gm._vrt_zoom_and_bounds("x.vrt", {})
    assert zoom == 17
    assert aligned is False
    assert bounds == [-112.0, 39.0, -111.5, 39.4]


@pytest.mark.parametrize("xres,zoom,aligned", [
    (gm._WEBMERC_Z0_MPP / 2 ** 17, 17, True),            # web-optimized COG: exactly on z17
    (gm._WEBMERC_Z0_MPP / 2 ** 17 * 1.005, 17, True),    # within tolerance
    (0.9, 17, False),                                     # 0.9 m/px rounds to z17 but is finer
])
def test_vrt_zoom_and_bounds_flags_whether_the_source_is_on_the_zoom_grid(xres, zoom, aligned):
    stdout = json.dumps({"geoTransform": [0, xres, 0, 0, 0, -xres]})
    with patch.object(gm.subprocess, "run", return_value=MagicMock(stdout=stdout)):
        z, a, _ = gm._vrt_zoom_and_bounds("x.vrt", {})
    assert (z, a) == (zoom, aligned)


def test_vrt_zoom_and_bounds_falls_back_to_utah_without_extent():
    """A VRT that reports no wgs84Extent falls back to the statewide clip instead of crashing."""
    xres = gm._WEBMERC_Z0_MPP / (2 ** 14)
    stdout = json.dumps({"geoTransform": [0, xres, 0, 0, 0, -xres]})
    with patch.object(gm.subprocess, "run", return_value=MagicMock(stdout=stdout)):
        zoom, aligned, bounds = gm._vrt_zoom_and_bounds("x.vrt", {})
    assert zoom == 14
    assert aligned is True
    assert bounds == list(gm.UTAH_BBOX)


def test_build_tier_tiling_argv_resampling_and_real_bounds():
    """build_tier's `gdal raster tile` argv: -r nearest at/above the mosaic's native zoom (the aligned
    masters copy through pixel-exact), -r average when a tier caps BELOW native; the full provisional
    flag set is always passed (guards a silent typo on a GDAL bump); and the mosaic's REAL bounds
    (not the statewide UTAH_BBOX) reach the packer."""
    by_sid = {"M-1": {"pub_year": "2022"}}
    extent = [-112.0, 39.0, -111.5, 39.4]
    calls: list[list[str]] = []
    captured: dict = {}

    def rec(cmd, *a, **k):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_pack(tiledir, mbtiles, *, bounds, name):
        captured["bounds"] = bounds
        return (10, min(gm.MOSAIC_MINZOOM, 17), 17)

    def tile_argv(maxz, aligned=True):
        calls.clear()
        with patch.object(gm.subprocess, "run", side_effect=rec), \
             patch.object(gm, "_vrt_zoom_and_bounds", return_value=(17, aligned, extent)), \
             patch.object(gm, "_pack_tiles_to_mbtiles", side_effect=fake_pack), \
             patch.object(gm.os.path, "getsize", return_value=1 << 20), \
             patch.object(gm.os, "remove"), patch.object(gm.shutil, "rmtree"), \
             patch.object(gm.gcs, "upload"):
            gm.build_tier("24k", ["M-1"], by_sid, maxz=maxz, write_stac_item=False)
        return next(c for c in calls if c[:3] == ["gdal", "raster", "tile"])

    tile = tile_argv(17)
    assert tile[tile.index("--resampling") + 1] == "nearest"          # at native
    for flag in ("--overview-resampling", "--skip-blank", "--min-zoom", "--max-zoom", "-f", "--co"):
        assert flag in tile, f"missing {flag}"
    assert tile[tile.index("-f") + 1] == "WEBP"
    assert tile[tile.index("--co") + 1] == f"QUALITY={gm.TILE_QUALITY}"
    assert tile[tile.index("--max-zoom") + 1] == "17"
    assert captured["bounds"] == extent                               # real footprint, not UTAH_BBOX

    t12 = tile_argv(12)
    assert t12[t12.index("--resampling") + 1] == "average"            # capped below native

    off = tile_argv(17, aligned=False)
    assert off[off.index("--resampling") + 1] == "average"            # native zoom, but off-grid source

    up = tile_argv(18)
    assert up[up.index("--resampling") + 1] == "nearest"              # on-grid, upsampled past native


@pytest.mark.parametrize("built,scales,rc", [
    (3, ["24k", "250k", "500k"], 0),
    (2, ["24k", "250k", "500k"], 1),    # one empty tier fails the job instead of reporting success
    (0, ["24k"], 1),
])
def test_main_exit_code_requires_every_requested_tier(built, scales, rc):
    argv = ["geolmap_mosaics", "--scale", "all" if len(scales) > 1 else scales[0]]
    with patch.object(gm.sys, "argv", argv), patch.object(gm, "build", return_value=built):
        assert gm.main() == rc
