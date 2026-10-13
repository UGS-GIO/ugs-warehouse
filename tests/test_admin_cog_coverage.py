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


def test_extract_map_layers_offers_the_pmtiles_and_relations_backfills(ops_jobs):
    """The vectors job runs `python`, so the backfill's args name the module, as the deployed job's
    `-m,ugs_warehouse.pubs.vectors,--all` does."""
    job = ops_jobs.JOBS["vectors"]
    assert ("pmtiles-backfill", "Build map tiles (PMTiles backfill)") in job.modes
    assert "PMTiles" in job.description
    res = ops_jobs.run_mode("vectors", "pmtiles-backfill")
    assert res["ok"] and res["dry_run"]
    assert res["message"].endswith("ugs-pubs-vectors -m ugs_warehouse.pubs.vectors --pmtiles-backfill")
    assert ("relations-backfill", "Record relationships (backfill)") in job.modes
    res = ops_jobs.run_mode("vectors", "relations-backfill")
    assert res["message"].endswith("ugs-pubs-vectors -m ugs_warehouse.pubs.vectors --relations-backfill")


def _entry(id_, self_href, *, cog=False, pub=None):
    props = {"ugs:in_publication": pub, "ugs:publication_title": "T"} if pub else {}
    assets = {"cog": {"href": f"https://x/{id_}.cog.tif", "type": COG}} if cog else {}
    return {"id": id_, "properties": props, "assets": assets,
            "links": [{"rel": "self", "href": self_href}]}


def test_coverage_reads_collection_layout_series_from_the_root_index(ops_stac, monkeypatch):
    docs = {
        f"{BASE}/ugs-publications/catalog.json": {"links": [
            {"rel": "child", "href": "./OFR/catalog.json"},        # collection layout
            {"rel": "child", "href": "./B/collection.json"},       # old layout
        ]},
        # The series index lists publications, which carry no COG; it must not be scanned.
        f"{BASE}/ugs-publications/OFR/items.json": {"items": [_entry("OFR-1", "./OFR-1/collection.json")]},
        f"{BASE}/ugs-publications/B/items.json": {"items": [
            _entry("B-1", "./B-1/B-1.json", cog=True), _entry("B-2", "./B-2/B-2.json")]},
        f"{BASE}/items.json": {"items": [
            _entry("OFR-1", "./ugs-publications/OFR/OFR-1/collection.json"),          # report entry
            _entry("OFR-1_plate", "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
                   cog=True, pub="OFR-1"),
            _entry("OFR-1_units", "./ugs-publications/OFR/OFR-1/OFR-1_units/OFR-1_units.json",
                   pub="OFR-1"),
            # A single-file publication keeps its plate on the Collection.
            _entry("OFR-2", "./ugs-publications/OFR/OFR-2/collection.json", cog=True),
            # Old-layout and other-collection entries are counted elsewhere or not at all.
            _entry("B-1", "./ugs-publications/B/B-1/B-1.json", cog=True),
            _entry("X-1_plate", "./ugs-external/OFR/X-1/X-1_plate/X-1_plate.json", cog=True, pub="X-1"),
        ]},
    }
    monkeypatch.setattr(ops_stac, "_get", lambda url: docs.get(url))
    monkeypatch.setattr(ops_stac, "PUB_COLLECTIONS", ["ugs-publications"])

    cov = ops_stac._cog_coverage()

    (row,) = cov["collections"]
    assert row == {"id": "ugs-publications", "items": 5, "cogs": 3,
                   "cog_ids": ["B-1", "OFR-1_plate", "OFR-2"]}
    assert (cov["total_items"], cov["total_cogs"]) == (5, 3)


def test_coverage_flags_a_missing_root_index(ops_stac, monkeypatch):
    docs = {f"{BASE}/ugs-publications/catalog.json": {"links": [{"rel": "child", "href": "./OFR/catalog.json"}]}}
    monkeypatch.setattr(ops_stac, "_get", lambda url: docs.get(url))
    monkeypatch.setattr(ops_stac, "PUB_COLLECTIONS", ["ugs-publications"])

    (row,) = ops_stac._cog_coverage()["collections"]
    assert row["error"] is True and row["items"] == 0
