"""The post-deploy smoke check asks the servers past the CDN's copy, but reads the catalog as is."""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import smoke_geoservices as smoke


def _urls(monkeypatch) -> list[str]:
    seen: list[str] = []

    @contextlib.contextmanager
    def urlopen(req, timeout):
        seen.append(req.full_url)
        yield SimpleNamespace(status=200, headers={}, read=lambda: b"{}")
    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    return seen


def test_server_requests_bypass_the_cdn_copy(monkeypatch):
    seen = _urls(monkeypatch)
    smoke.fetch("https://api.example/ugs/rest/services/x/default/FeatureServer/0?f=json")
    smoke.fetch("https://api.example/ugs/rest/services/M-103/ImageServer/tile/1/2/3")
    assert seen == [
        f"https://api.example/ugs/rest/services/x/default/FeatureServer/0?f=json&_smoke={smoke.RUN}",
        f"https://api.example/ugs/rest/services/M-103/ImageServer/tile/1/2/3?_smoke={smoke.RUN}"]


def test_the_catalog_is_read_unchanged(monkeypatch):
    seen = _urls(monkeypatch)
    smoke.fetch(smoke.ITEMS_URL)
    assert seen == [smoke.ITEMS_URL]


def test_search_sources_need_plss_and_a_geologic_unit_item():
    items = [{"id": i} for i in (*smoke.PLSS_ITEMS, "geolmap_geolunits_500k")]
    smoke.check_search_sources(items)
    for drop in (smoke.PLSS_ITEMS[0], "geolmap_geolunits_500k"):
        with pytest.raises(AssertionError):
            smoke.check_search_sources([i for i in items if i["id"] != drop])


def test_search_sources_match_the_viewer():
    viewer = Path(__file__).parents[1] / "viewer/src/map"
    plss = re.findall(r'export const PLSS_\w+ = "(\w+)"', (viewer / "plss.ts").read_text())
    assert sorted(plss) == sorted(smoke.PLSS_ITEMS)
    assert f"/{smoke.GEOLUNITS}/" in (viewer / "unit-search.ts").read_text()
