from __future__ import annotations

import json

from scripts import georef_quads as g


def _maps():
    return json.loads(g.MANIFEST.read_text())["maps"]


def test_every_map_is_placed_one_way():
    for m in _maps():
        ways = [k for k in ("corners", "gcps", "world_file") if k in m]
        assert len(ways) <= 1, m["series"]
        if "corners" in m:
            assert len(m["corners"]) in (3, 4) and len(m["bounds"]) == 4, m["series"]
            w, s, e, n = m["bounds"]
            assert w < e and s < n, m["series"]
        assert ("page" in m) != ("member" in m), m["series"]
    assert len({m["series"] for m in _maps()}) == len(_maps())


def test_corners_tie_to_the_sheet_corners_clockwise_from_top_left():
    m = {"corners": [[1, 2], [3, 4], [5, 6], [7, 8]], "bounds": [-113.125, 37.625, -113.0, 37.75]}
    args = g.gcp_args(m, "EPSG:4267")
    assert args[:2] == ["-a_srs", "EPSG:4267"]
    assert args[2:7] == ["-gcp", "1", "2", "-113.125", "37.75"]
    assert args[-5:] == ["-gcp", "7", "8", "-113.125", "37.625"]
    assert g.warp_args(m) == ["-tps"]


def test_three_corners_fit_affine_and_gcp_lists_fit_a_surface():
    assert g.warp_args({"corners": [[0, 0]] * 3}) == ["-order", "1"]
    m = {"gcps": [[1, 2, 300.0, 400.0]], "gcp_srs": "EPSG:26912"}
    assert g.gcp_args(m, "EPSG:4267") == ["-a_srs", "EPSG:26912", "-gcp", "1", "2", "300.0", "400.0"]
    assert g.warp_args(m)[:2] == ["-order", "2"]
    assert g.gcp_args({}, "EPSG:4267") == [] and g.warp_args({}) == []


def test_world_file_places_the_outer_edge_of_the_top_left_pixel(tmp_path):
    tfw = tmp_path / "a.tfw"
    tfw.write_text("2\n0\n0\n-2\n101\n199\n")
    assert g.world_file_args(str(tfw), (10, 5), "EPSG:26712") == [
        "-a_srs", "EPSG:26712", "-a_ullr", "100.0", "200.0", "120.0", "190.0"]
