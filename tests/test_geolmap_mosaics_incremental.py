import math
import shutil
import time

import pytest
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import zxy_to_tileid

from ugs_warehouse.pubs import geolmap_mosaics as gm


def _rec(etag, bbox=(0.0, 0.0, 1.0, 1.0)):
    return {"etag": etag, "bbox": list(bbox)}


BUILD = {"minzoom": 4, "maxzoom": 14}


def test_changed_members_names_added_rewritten_and_removed_cogs(monkeypatch):
    monkeypatch.setattr(gm, "INCREMENTAL_MAX_FRACTION", 0.75)
    prev = {"A": _rec("1"), "B": _rec("1"), "C": _rec("1"), "D": _rec("1")}
    assert gm.changed_members(prev, BUILD, {"A": "1", "B": "2", "C": "1", "E": "1"},
                              BUILD) == ["B", "D", "E"]
    assert gm.changed_members(prev, BUILD, {"A": "1", "B": "2", "C": "1", "D": "1"}, BUILD) == ["B"]
    assert gm.changed_members(prev, BUILD, {"A": "1", "B": "1", "C": "1"}, BUILD) == ["D"]
    assert gm.changed_members(prev, BUILD, {**dict.fromkeys("ABCD", "1"), "E": "1"}, BUILD) == ["E"]
    assert gm.changed_members(prev, BUILD, dict.fromkeys("ABCD", "1"), BUILD) == []


@pytest.mark.parametrize("prev,prev_build", [
    (None, BUILD),                                           # never recorded
    ({}, BUILD),
    ({"A": _rec("1"), "B": _rec("1")}, {**BUILD, "maxzoom": 15}),   # other zoom range
])
def test_changed_members_asks_for_a_full_build_without_a_matching_record(prev, prev_build):
    assert gm.changed_members(prev, prev_build, {"A": "1", "B": "1"}, BUILD) is None


def test_changed_members_asks_for_a_full_build_when_too_many_changed(monkeypatch):
    monkeypatch.setattr(gm, "INCREMENTAL_MAX_FRACTION", 0.3)
    prev = {s: _rec("1") for s in "ABCDEFGHIJ"}
    assert gm.changed_members(prev, BUILD, {**dict.fromkeys("ABCDEFGHIJ", "1"), "A": "2", "B": "2",
                                            "C": "2"}, BUILD) == ["A", "B", "C"]
    assert gm.changed_members(prev, BUILD, {**dict.fromkeys("ABCDEFGHIJ", "1"), "A": "2", "B": "2",
                                            "C": "2", "D": "2"}, BUILD) is None


def _bbox_of(rect, z):
    """[W, S, E, N] of a tile range, exactly on its tile edges."""
    n = 2 ** z

    def lat(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    x0, y0, x1, y1 = rect
    return [x0 / n * 360 - 180, lat(y1 + 1), (x1 + 1) / n * 360 - 180, lat(y0)]


def test_tile_range_keeps_a_box_on_tile_edges_to_its_own_tiles():
    rect = (3100, 6200, 3103, 6203)
    assert gm._tile_range(_bbox_of(rect, 14), 14) == rect
    # The parents of those tiles, at every zoom below.
    assert gm._tile_range(_bbox_of(rect, 14), 13) == (1550, 3100, 1551, 3101)
    assert gm._tile_range(_bbox_of(rect, 14), 4) == (3, 6, 3, 6)
    # Just inside an edge touches the edge tile; just past it touches the neighbor.
    w, s, e, n = _bbox_of(rect, 14)
    assert gm._tile_range([w + 1e-4, s, e - 1e-4, n], 14) == rect
    assert gm._tile_range([w - 1e-4, s, e + 1e-4, n], 14) == (3099, 6200, 3104, 6203)


def test_dirty_rects_cover_every_zoom_and_join_touching_ranges():
    a = _bbox_of((100, 100, 101, 101), 10)
    b = _bbox_of((102, 100, 103, 101), 10)      # touches a on the right
    c = _bbox_of((200, 200, 200, 200), 10)      # far away
    d = gm.dirty_rects([a, b, c], 8, 10)
    assert d[10] == [(100, 100, 103, 101), (200, 200, 200, 200)]
    assert d[9] == [(50, 50, 51, 50), (100, 100, 100, 100)]
    assert d[8] == [(25, 25, 25, 25), (50, 50, 50, 50)]
    assert sorted(d) == [8, 9, 10]


def test_merge_rects_chains_through_a_range_that_joins_two():
    assert gm._merge_rects([(0, 0, 0, 0), (5, 0, 5, 0), (1, 0, 4, 0)]) == [(0, 0, 5, 0)]


def _archive(path, tiles):
    gm._pack(str(path), sorted((zxy_to_tileid(*k), v) for k, v in tiles.items()),
             bounds=[-1, -1, 1, 1], name="t")


def _read(path):
    with open(path, "rb") as f:
        return dict(all_tiles(MmapSource(f)))


def test_merged_tiles_copies_untouched_replaces_dirty_and_drops_blank(tmp_path):
    old = {(2, 0, 0): b"keep-z2", (3, 0, 0): b"keep", (3, 1, 1): b"old-1", (3, 2, 1): b"old-2",
           (3, 7, 7): b"keep-far"}
    _archive(tmp_path / "old.pmtiles", old)
    tree = tmp_path / "t"
    (tree / "3" / "1").mkdir(parents=True)
    (tree / "3" / "1" / "1.webp").write_bytes(b"new-1")        # re-rendered
    (tree / "3" / "3").mkdir(parents=True)
    (tree / "3" / "3" / "2.webp").write_bytes(b"new-3")        # new coverage
    dirty = {3: [(1, 1, 3, 2)]}                                 # (3,2,1) is now blank

    n, minz, maxz = gm._pack(str(tmp_path / "new.pmtiles"),
                             gm._merged_tiles(str(tmp_path / "old.pmtiles"), str(tree), dirty),
                             bounds=[-1, -1, 1, 1], name="t")
    got = _read(tmp_path / "new.pmtiles")
    assert got == {(2, 0, 0): b"keep-z2", (3, 0, 0): b"keep", (3, 1, 1): b"new-1",
                   (3, 3, 2): b"new-3", (3, 7, 7): b"keep-far"}
    assert (n, minz, maxz) == (5, 2, 3)
    with open(tmp_path / "new.pmtiles", "rb") as f:
        assert Reader(MmapSource(f)).header()["clustered"]
    assert not list(tree.rglob("*.webp"))                      # each tile file is consumed


def test_plan_falls_back_to_full_when_a_changed_member_has_no_footprint(monkeypatch, capsys):
    monkeypatch.setattr(gm, "INCREMENTAL_MAX_FRACTION", 1.0)
    prev = {"A": _rec("1"), "B": _rec("1")}
    monkeypatch.setattr(gm.stac, "prior_property",
                        lambda _c, _i, prop: prev if prop == gm.MEMBERS_PROP else BUILD)
    monkeypatch.setattr(gm, "_cog_bboxes", lambda sids, env: {s: None for s in sids})
    etags = {f"{gm.identity.COG_PREFIX}/{s}.cog.tif": e for s, e in (("A", "1"), ("B", "2"))}
    members, boxes = gm._plan("24k", ["A", "B"], {}, BUILD, True, etags)
    assert boxes is None and members["A"]["bbox"] == [0.0, 0.0, 1.0, 1.0] and members["B"]["bbox"] is None
    assert "no footprint" in capsys.readouterr().out


def test_plan_reads_only_the_changed_footprints(monkeypatch):
    monkeypatch.setattr(gm, "INCREMENTAL_MAX_FRACTION", 1.0)
    prev = {"A": _rec("1", (0, 0, 1, 1)), "B": _rec("1", (2, 2, 3, 3))}
    monkeypatch.setattr(gm.stac, "prior_property",
                        lambda _c, _i, prop: prev if prop == gm.MEMBERS_PROP else BUILD)
    read = []
    monkeypatch.setattr(gm, "_cog_bboxes",
                        lambda sids, env: read.extend(sids) or {s: [5, 5, 6, 6] for s in sids})
    etags = {f"{gm.identity.COG_PREFIX}/{s}.cog.tif": e for s, e in (("A", "1"), ("B", "2"))}
    members, boxes = gm._plan("24k", ["A", "B"], {}, BUILD, True, etags)
    assert read == ["B"]
    assert boxes == [[2, 2, 3, 3], [5, 5, 6, 6]]               # B's old and new footprints
    assert members == {"A": _rec("1", (0, 0, 1, 1)), "B": {"etag": "2", "bbox": [5, 5, 6, 6]}}


# ---------------------------------------------------------------- GDAL integration

GDAL = all(shutil.which(c) for c in ("gdal", "gdalbuildvrt", "gdalinfo", "gdal_translate"))
Z = 14
X0, Y0 = 3100, 6200          # z14 tiles in Utah


def _cog(path, tx, ty, ntx, nty, seed):
    """A web-optimized RGBA COG on the z14 grid covering ntx x nty tiles from (tx, ty)."""
    import subprocess

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    res = gm._WEBMERC_Z0_MPP / 2 ** Z
    w, h = ntx * 256, nty * 256
    rng = np.random.default_rng(seed)
    a = np.full((4, h, w), 255, "uint8")
    a[:3] = (rng.random((3, h // 32, w // 32)) * 255).astype("uint8").repeat(32, 1).repeat(32, 2)
    raw = f"{path}.raw.tif"
    with rasterio.open(raw, "w", driver="GTiff", width=w, height=h, count=4, dtype="uint8",
                       crs="EPSG:3857", photometric="RGB", alpha="YES",
                       transform=from_origin(-gm._WEBMERC_ORIGIN + tx * 256 * res,
                                             gm._WEBMERC_ORIGIN - ty * 256 * res, res, res)) as d:
        d.write(a)
    subprocess.run(["gdal_translate", "-q", raw, str(path), "-of", "COG", "-co", "COMPRESS=DEFLATE",
                    "-co", "TILING_SCHEME=GoogleMapsCompatible", "-co", f"ZOOM_LEVEL={Z}"], check=True)


def _fetch(obj, dest):
    gm.gcs.download(obj, str(dest))
    return str(dest)


@pytest.mark.skipif(not GDAL, reason="needs the GDAL 3.11+ CLIs")
def test_incremental_rebake_matches_a_full_one_and_keeps_untouched_tiles(tmp_path, monkeypatch, capsys):
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from rio_cogeo.cogeo import cog_validate

    cogs = tmp_path / "cogs"
    cogs.mkdir()
    _cog(cogs / "A.cog.tif", X0, Y0, 4, 4, 1)
    _cog(cogs / "B.cog.tif", X0 + 4, Y0, 4, 4, 2)
    _cog(cogs / "C.cog.tif", X0 + 1, Y0 + 4, 3, 2, 3)   # added later, below A and B
    monkeypatch.setattr(gm, "_vsigs", lambda sid: str(cogs / f"{sid}.cog.tif"))
    monkeypatch.setattr(gm, "MOSAIC_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(gm, "INCREMENTAL_MAX_FRACTION", 0.5)
    monkeypatch.setattr(gm, "UTAH_BBOX", _bbox_of((X0 - 2, Y0 - 2, X0 + 10, Y0 + 8), Z))
    published = gm.PublishedPubs(frozenset(), frozenset())

    def bake(sids, name, **kw):
        etags = {f"{gm.identity.COG_PREFIX}/{s}.cog.tif": "v1" for s in sids}
        t = time.perf_counter()
        assert gm.build_tier("24k", sids, {}, etags=etags, published=published, **kw)
        took = time.perf_counter() - t
        out = capsys.readouterr().out
        d = tmp_path / name
        d.mkdir()
        return (_fetch(gm.mosaic_object("24k"), d / "m.pmtiles"),
                _fetch(gm.mosaic_object("24k", ext="cog.tif"), d / "m.cog.tif"), took, out)

    p1, c1, _, _ = bake(["A", "B"], "first")
    p2, c2, t_inc, out = bake(["A", "B", "C"], "incremental")
    assert "incremental, 1 changed: C" in out
    p3, c3, t_full, out = bake(["A", "B", "C"], "full", full=True)
    assert "rendering the changed tiles" not in out
    with capsys.disabled():
        print(f"\nsynthetic 3-map tier: full {t_full:.1f}s, incremental {t_inc:.1f}s")

    old, inc, full = _read(p1), _read(p2), _read(p3)
    dirty = gm.dirty_rects([gm._cog_bboxes(["C"], {})["C"]], min(gm.MOSAIC_MINZOOM, Z), Z)
    untouched = {k for k in old if not gm._in_rects(dirty.get(k[0], ()), k[1], k[2])}
    assert untouched and all(inc[k] == old[k] for k in untouched)       # copied byte for byte
    assert set(inc) == set(full)                                        # new area present
    assert all(inc[k] == full[k] for k in inc if k[0] == Z)             # base tiles pixel-exact
    # Overviews: GDAL's pyramid edges depend on the run's tile range, so full bakes differ too.
    for k in inc:
        if k[0] < Z and inc[k] != full[k]:
            a = np.asarray(_decode(inc[k]), float)
            b = np.asarray(_decode(full[k]), float)
            assert np.abs(a - b).mean() < 0.5, k

    assert cog_validate(c2)[0]
    with rasterio.open(c1) as r1, rasterio.open(c2) as r2, rasterio.open(c3) as r3:
        assert (r2.read() == r3.read()).all()                          # same pixels as a full bake
        # C lies below the first bake's extent, so all of that extent is unchanged.
        assert (r1.bounds.left, r1.bounds.top) == (r2.bounds.left, r2.bounds.top)
        assert r2.height > r1.height
        assert (r1.read() == r2.read(window=Window(0, 0, r1.width, r1.height))).all()

    # Removing a map clears its ground: B's tiles go, A's stay.
    p4, c4, _, out = bake(["A", "C"], "removed")
    assert "incremental, 1 changed: B" in out
    gone = _read(p4)
    assert not any(k[0] == Z and k[1] >= X0 + 4 and k[2] < Y0 + 4 for k in gone)
    assert all(gone[k] == inc[k] for k in inc if k[0] == Z and k[1] < X0 + 4)
    with rasterio.open(c4) as r4:
        assert r4.width == 4 * 256                                     # the COG shrinks to A and C
    item = gm.stac.prior_property(gm.COLLECTION, "geologic-maps-24k", gm.MEMBERS_PROP)
    assert sorted(item) == ["A", "C"]

    etags = {f"{gm.identity.COG_PREFIX}/{s}.cog.tif": "v1" for s in "AC"}
    assert gm.build_tier("24k", ["A", "C"], {}, etags=etags, published=published)
    assert "no member changed" in capsys.readouterr().out


def _decode(blob):
    import warnings

    import rasterio

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.MemoryFile(blob) as mem, mem.open() as ds:
            return ds.read()
