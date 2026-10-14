from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

COG = "image/tiff; application=geotiff; profile=cloud-optimized"
BASE = "https://cdn/stac"


@pytest.fixture
def ops_stac(monkeypatch):
    """admin/ops/stac.py loaded by path, with `django.conf.settings` stubbed: the coverage scan
    reads only `STAC_BASE`, and Django is not installed in the test env."""
    conf = types.ModuleType("django.conf")
    conf.settings = types.SimpleNamespace(STAC_BASE=BASE)
    monkeypatch.setitem(sys.modules, "django", types.ModuleType("django"))
    monkeypatch.setitem(sys.modules, "django.conf", conf)
    path = Path(__file__).resolve().parents[1] / "admin" / "ops" / "stac.py"
    spec = importlib.util.spec_from_file_location("ops_stac_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ops_jobs(monkeypatch):
    """admin/ops/jobs.py loaded by path with `django.conf.settings` stubbed (dry-run jobs)."""
    conf = types.ModuleType("django.conf")
    conf.settings = types.SimpleNamespace(JOBS_DRY_RUN=True, GCP_PROJECT="p", GCP_REGION="us-central1")
    monkeypatch.setitem(sys.modules, "django", types.ModuleType("django"))
    monkeypatch.setitem(sys.modules, "django.conf", conf)
    path = Path(__file__).resolve().parents[1] / "admin" / "ops" / "jobs.py"
    spec = importlib.util.spec_from_file_location("ops_jobs_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ops_jobs_under_test", mod)  # @dataclass looks its module up
    spec.loader.exec_module(mod)
    return mod


def test_extract_map_layers_has_no_backfill_buttons(ops_jobs):
    """The regular run fills in tiles and relationships itself, so the job offers no backfill."""
    job = ops_jobs.JOBS["vectors"]
    assert not job.modes
    assert "vectors" not in ops_jobs.MODE_ARGS
    assert "pending" in job.description
    assert not ops_jobs.run_mode("vectors", "pmtiles-backfill")["ok"]


def _entry(id_, self_href, *, cog=False):
    assets = {"cog": {"href": f"https://x/{id_}.cog.tif", "type": COG}} if cog else {}
    return {"id": id_, "properties": {}, "assets": assets,
            "links": [{"rel": "self", "href": self_href}]}


def _summary(**series):
    return {"type": "ugs-pub-coverage", "groups": {"ugs-publications": series}}


def test_coverage_reads_collection_layout_series_from_the_refresh_summary(ops_stac, monkeypatch):
    docs = {
        f"{BASE}/ugs-publications/catalog.json": {"links": [
            {"rel": "child", "href": "./OFR/catalog.json"},        # collection layout
            {"rel": "child", "href": "./B/collection.json"},       # old layout
        ]},
        f"{BASE}/ugs-publications/B/items.json": {"items": [
            _entry("B-1", "./B-1/B-1.json", cog=True), _entry("B-2", "./B-2/B-2.json")]},
        f"{BASE}/pub-coverage.json": _summary(OFR={
            "publications": 2, "migrated": True, "pending": 0, "items": 3,
            "cog_ids": ["OFR-1_plate", "OFR-2"]}),
    }
    fetched: list[str] = []
    monkeypatch.setattr(ops_stac, "_get", lambda url: fetched.append(url) or docs.get(url))
    monkeypatch.setattr(ops_stac, "PUB_COLLECTIONS", ["ugs-publications"])

    cov = ops_stac._cog_coverage()

    (row,) = cov["collections"]
    assert row == {"id": "ugs-publications", "items": 5, "cogs": 3,
                   "cog_ids": ["B-1", "OFR-1_plate", "OFR-2"]}
    assert (cov["total_items"], cov["total_cogs"]) == (5, 3)
    # The panel never fetches the root index, nor the migrated series' publication index.
    assert f"{BASE}/items.json" not in fetched
    assert f"{BASE}/ugs-publications/OFR/items.json" not in fetched


def test_coverage_flags_a_missing_summary(ops_stac, monkeypatch):
    docs = {f"{BASE}/ugs-publications/catalog.json": {"links": [{"rel": "child", "href": "./OFR/catalog.json"}]}}
    monkeypatch.setattr(ops_stac, "_get", lambda url: docs.get(url))
    monkeypatch.setattr(ops_stac, "PUB_COLLECTIONS", ["ugs-publications"])

    (row,) = ops_stac._cog_coverage()["collections"]
    assert row["error"] is True and row["items"] == 0


def test_coverage_flags_a_summary_older_than_a_series_migration(ops_stac, monkeypatch):
    docs = {
        f"{BASE}/ugs-publications/catalog.json": {"links": [{"rel": "child", "href": "./OFR/catalog.json"},
                                                            {"rel": "child", "href": "./M/catalog.json"}]},
        f"{BASE}/pub-coverage.json": _summary(OFR={"publications": 1, "migrated": True, "pending": 0,
                                                   "items": 1, "cog_ids": ["OFR-1"]}),
    }
    monkeypatch.setattr(ops_stac, "_get", lambda url: docs.get(url))
    monkeypatch.setattr(ops_stac, "PUB_COLLECTIONS", ["ugs-publications"])

    (row,) = ops_stac._cog_coverage()["collections"]
    assert row["error"] is True


def test_get_parses_the_body_without_a_decoded_copy(ops_stac, monkeypatch):
    import gzip
    import io
    import json

    body = gzip.compress(json.dumps({"ok": 1}).encode())
    monkeypatch.setattr(ops_stac.urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(body))
    assert ops_stac._get("https://x/a.json") == {"ok": 1}
