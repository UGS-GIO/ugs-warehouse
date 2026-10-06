"""How the ugs-styles manifest is READ (source + cache freshness), as distinct from how it's bound.

Kept out of test_styles.py on purpose: that module's autouse fixture stubs `_manifest` wholesale,
which is exactly the function under test here.

Background: the manifest is published to our own bucket and also served through the CDN. Reading the
CDN copy is what let a rebind bind a pre-publish manifest — ugs-styles' publish rsyncs, sets a 5-min
max-age, then triggers the rebind seconds later, so the edge can still be serving the OLD manifest
when we read it. The job then writes that stale legend onto every item and exits 0.
"""
from ugs_warehouse.core import styles


class _FakeResp:
    """Minimal urlopen() context-manager stand-in."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_manifest_prefers_the_gcs_object(monkeypatch):
    monkeypatch.setattr(styles, "_cache", None)
    monkeypatch.setattr(styles.gcs, "get_bytes", lambda p: b'[{"itemId": "from_gcs", "render": "default"}]')

    def _boom(*a, **k):  # the CDN must not be consulted when the object is readable
        raise AssertionError("read the CDN despite the GCS object being available")

    monkeypatch.setattr(styles.urllib.request, "urlopen", _boom)
    assert [e["itemId"] for e in styles._manifest()] == ["from_gcs"]


def test_manifest_falls_back_to_https_without_bucket_access(monkeypatch):
    """Local tooling and tests run without bucket credentials — they still get a manifest."""
    monkeypatch.setattr(styles, "_cache", None)

    def _no_creds(_p):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(styles.gcs, "get_bytes", _no_creds)
    monkeypatch.setattr(styles.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(b'[{"itemId": "from_cdn", "render": "default"}]'))
    assert [e["itemId"] for e in styles._manifest()] == ["from_cdn"]


def test_unreachable_manifest_is_empty_not_an_error(monkeypatch):
    """Styling is best-effort: a dead manifest must never sink an ingest."""
    monkeypatch.setattr(styles, "_cache", None)

    def _fail(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(styles.gcs, "get_bytes", _fail)
    monkeypatch.setattr(styles.urllib.request, "urlopen", _fail)
    assert styles._manifest() == ()


def _counting_gcs(monkeypatch):
    """Manifest whose content changes on every read, so a cache hit is observable."""
    calls = {"n": 0}

    def _changing(_p):
        calls["n"] += 1
        return b'[{"itemId": "v%d", "render": "default"}]' % calls["n"]

    monkeypatch.setattr(styles.gcs, "get_bytes", _changing)
    return calls


def test_manifest_cache_expires_so_a_long_run_cannot_pin_a_stale_snapshot(monkeypatch):
    """An ingest that starts before a style publish must not re-attach the old manifest all run."""
    monkeypatch.setattr(styles, "_cache", None)
    _counting_gcs(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(styles.time, "monotonic", lambda: now[0])

    assert styles._manifest()[0]["itemId"] == "v1"
    now[0] += styles._TTL_SECONDS / 2
    assert styles._manifest()[0]["itemId"] == "v1"   # inside the TTL: served from cache
    now[0] += styles._TTL_SECONDS
    assert styles._manifest()[0]["itemId"] == "v2"   # expired: the re-read sees the publish


def test_refresh_drops_the_cache_immediately(monkeypatch):
    """restyle() runs seconds after a publish, so it re-reads rather than trusting the TTL."""
    monkeypatch.setattr(styles, "_cache", None)
    calls = _counting_gcs(monkeypatch)
    styles._manifest()
    assert styles.refresh() == 1
    assert calls["n"] == 2


def test_manifest_skips_collection_bound_entries(monkeypatch):
    monkeypatch.setattr(styles, "_cache", None)
    monkeypatch.setattr(styles.gcs, "get_bytes", lambda p: (
        b'[{"itemId": "hazards_qfaults", "render": "default"},'
        b' {"collectionId": "ubm-ensemble-raster", "render": "default", "layer": "ubm_ensemble_raster"}]'))
    assert [styles._entry_key(e) for e in styles._manifest()] == ["hazards_qfaults"]
