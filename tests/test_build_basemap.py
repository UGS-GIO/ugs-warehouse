from __future__ import annotations

import io
import json

from scripts import build_basemap
from ugs_warehouse.basemap import utah_quads


def test_extract_covers_the_whole_quad_grid():
    # Includes the sliver column past 114W.
    west, south, east, north = map(float, build_basemap.grid_bbox(list(utah_quads())).split(","))
    assert (west, south, east, north) == (-114.125, 37.0, -109.0, 42.0)


def test_latest_build_is_the_newest_daily_key(monkeypatch):
    builds = [{"key": "20260922.pmtiles"}, {"key": "20260924.pmtiles"}, {"key": "20260923.pmtiles"},
              {"key": "zz-notes.txt"}, {"size": 1}]
    seen = {}

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return io.BytesIO(json.dumps(builds).encode())

    monkeypatch.setattr(build_basemap.urllib.request, "urlopen", fake_urlopen)
    assert build_basemap.latest_build() == "20260924.pmtiles"
    assert seen["ua"], "the metadata host 403s Python's default User-Agent"
