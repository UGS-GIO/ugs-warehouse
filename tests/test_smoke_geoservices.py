"""The post-deploy smoke check asks the servers past the CDN's copy, but reads the catalog as is."""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

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
