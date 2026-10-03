"""Hermetic tests for ugs_warehouse.pubs.prune."""
from __future__ import annotations

import sys
from unittest.mock import patch

from ugs_warehouse.core import config
from ugs_warehouse.pubs import prune as P
from ugs_warehouse.pubs.prune import expected_pub_items, orphan_pub_paths


def test_expected_pub_items():
    pubs = [
        {"series_id": "M-100", "pub_publisher": "UGS"},
        {"series_id": "MD-50", "pub_publisher": ""},
        {"series_id": "EXT-1", "pub_publisher": "Foreign Press"},
        {"series_id": "Geologic map of Utah", "pub_publisher": "UGS"},
    ]
    expected = expected_pub_items(pubs)
    assert ("ugs-publications", "M", "M-100") in expected
    assert ("ugs-mining-district-files", "MD", "MD-50") in expected
    assert ("ugs-external", "EXT", "EXT-1") in expected
    # Space in id is converted to hyphen in expected item_id
    assert ("ugs-publications", "GEOLOGIC", "Geologic-map-of-Utah") in expected


def test_orphan_pub_paths():
    expected = {
        ("ugs-publications", "M", "M-100"),
        ("ugs-publications", "GEOLOGIC", "Geologic-map-of-Utah"),
    }

    mock_paths = {
        "warehouse/stac/ugs-publications/": [
            "warehouse/stac/ugs-publications/catalog.json",
            "warehouse/stac/ugs-publications/M/collection.json",
            "warehouse/stac/ugs-publications/M/M-100/M-100.json",
            "warehouse/stac/ugs-publications/M/M-100/M-100.iso.xml",
            # Orphan: uppercase twin that shouldn't be there
            "warehouse/stac/ugs-publications/M/M-100A/M-100A.json",
            "warehouse/stac/ugs-publications/M/M-100A/M-100A.iso.xml",
            # Orphan: old id with spaces before escaping
            "warehouse/stac/ugs-publications/GEOLOGIC/Geologic map of Utah/Geologic map of Utah.json",
            # Valid escaped id
            "warehouse/stac/ugs-publications/GEOLOGIC/Geologic-map-of-Utah/Geologic-map-of-Utah.json",
        ],
        "warehouse/stac/ugs-mining-district-files/": [],
        "warehouse/stac/ugs-external/": [],
    }

    def fake_list_paths(pfx: str):
        return mock_paths.get(pfx, [])

    with patch("ugs_warehouse.core.gcs.list_paths", side_effect=fake_list_paths):
        orphans = orphan_pub_paths(expected=expected)

    assert "warehouse/stac/ugs-publications/M/M-100A/M-100A.json" in orphans
    assert "warehouse/stac/ugs-publications/M/M-100A/M-100A.iso.xml" in orphans
    assert "warehouse/stac/ugs-publications/GEOLOGIC/Geologic map of Utah/Geologic map of Utah.json" in orphans
    assert "warehouse/stac/ugs-publications/M/M-100/M-100.json" not in orphans
    assert "warehouse/stac/ugs-publications/GEOLOGIC/Geologic-map-of-Utah/Geologic-map-of-Utah.json" not in orphans
    assert "warehouse/stac/ugs-publications/catalog.json" not in orphans
    assert "warehouse/stac/ugs-publications/M/collection.json" not in orphans


def test_dry_run_deletes_nothing_and_apply_deletes_each_orphan(monkeypatch):
    """The assertion that matters most for a script whose whole job is deleting objects: without
    --apply it reports and touches nothing."""
    orphans = [
        "warehouse/stac/ugs-publications/M/M-100A/M-100A.json",
        "warehouse/stac/ugs-publications/M/M-100A/M-100A.iso.xml",
    ]
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    expected = {("ugs-publications", "M", f"M-{n}") for n in range(100)}
    monkeypatch.setattr(P, "expected_pub_items", lambda: expected)
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: list(orphans))

    monkeypatch.setattr(sys, "argv", ["prune"])
    assert P.main() == 0
    assert deleted == []

    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 0
    assert deleted == orphans


def test_apply_refuses_when_the_source_looks_empty(monkeypatch):
    """An empty or partial source makes every published item look orphaned. Refuse, delete nothing."""
    orphans = [f"warehouse/stac/ugs-publications/M/M-{n}/M-{n}.json" for n in range(50)]
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    monkeypatch.setattr(P, "expected_pub_items", lambda: {("ugs-publications", "M", "M-0")})
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: list(orphans))
    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 1
    assert deleted == []


def test_a_case_twin_goes_and_the_correct_spelling_stays():
    """The bug this script exists for: two ids differing only in case, one of them legitimate.
    A rule like "drop the uppercase one" would take `MD-50`, so the comparison has to be against
    what the source actually produces."""
    expected = expected_pub_items([
        {"series_id": "MUS-Keetley", "pub_publisher": "UGS"},
        {"series_id": "MD-50", "pub_publisher": ""},
    ])
    published = {
        "warehouse/stac/ugs-publications/": [
            "warehouse/stac/ugs-publications/MUS/MUS-Keetley/MUS-Keetley.json",
            "warehouse/stac/ugs-publications/MUS/MUS-Keetley/MUS-Keetley.iso.xml",
            "warehouse/stac/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY.json",
            "warehouse/stac/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY.iso.xml",
        ],
        "warehouse/stac/ugs-mining-district-files/": [
            "warehouse/stac/ugs-mining-district-files/MD/MD-50/MD-50.json",
        ],
        "warehouse/stac/ugs-external/": [],
    }

    with patch("ugs_warehouse.core.gcs.list_paths", side_effect=lambda pfx: published.get(pfx, [])):
        orphans = orphan_pub_paths(expected=expected)

    assert orphans == ["warehouse/stac/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY.json",
                       "warehouse/stac/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY.iso.xml"]


def test_the_scan_never_leaves_the_publication_prefixes():
    """PUB_GROUPS is the only thing between this script and the serving topics."""
    seen: list[str] = []

    def record(prefix: str) -> list[str]:
        seen.append(prefix)
        return []

    with patch("ugs_warehouse.core.gcs.list_paths", side_effect=record):
        orphan_pub_paths(expected=set())

    assert seen == [f"{config.STAC_PREFIX}/{g}/" for g in P.PUB_GROUPS]
    assert not any("serving-topics" in p or "ugs-rasters" in p for p in seen)
