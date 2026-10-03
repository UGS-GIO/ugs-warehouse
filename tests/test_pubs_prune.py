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


def _catalog(monkeypatch, orphans: dict[str, str], pubs: list[dict]):
    """Orphan item dirs -> their publication file; the source lists `pubs`. Returns the deleted list."""
    import json

    paths = [p for d in orphans for p in (f"{d}/{d.rsplit('/', 1)[1]}.json", f"{d}/{d.rsplit('/', 1)[1]}.iso.xml")]
    docs = {f"{d}/{d.rsplit('/', 1)[1]}.json": json.dumps(
        {"assets": {"publication": {"href": f}}}).encode() for d, f in orphans.items()}
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    monkeypatch.setattr(P.gcs, "get_bytes", lambda path: docs[path])
    monkeypatch.setattr(P.source, "read_pubs", lambda: pubs)
    monkeypatch.setattr(P.source, "read_attachments", lambda: [])
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: list(paths))
    return deleted


ROOT = "warehouse/stac/ugs-publications/M"
SOURCE = [{"series_id": f"M-{n}", "pub_url": f"maps/m-{n}.pdf"} for n in range(100)]


def test_dry_run_deletes_nothing_and_apply_deletes_a_renamed_item(monkeypatch):
    """The assertion that matters most for a job whose whole job is deleting objects: without
    --apply it reports and touches nothing."""
    deleted = _catalog(monkeypatch, {f"{ROOT}/M-1A": "https://ugspub.nr.utah.gov/publications/maps/m-1.pdf"},
                       SOURCE)
    monkeypatch.setattr(sys, "argv", ["prune"])
    assert P.main() == 0
    assert deleted == []

    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 0
    assert deleted == [f"{ROOT}/M-1A/M-1A.json", f"{ROOT}/M-1A/M-1A.iso.xml"]


def test_an_orphan_whose_file_the_source_lacks_is_kept(monkeypatch):
    """A document the source does not list is missing from the source, not a stale copy."""
    deleted = _catalog(monkeypatch, {
        f"{ROOT}/M-1A": "https://ugspub.nr.utah.gov/publications/maps/m-1.pdf",
        f"{ROOT}/M-999": "https://ugspub.nr.utah.gov/publications/maps/legacy-scan.pdf"}, SOURCE)
    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 0
    assert all("/M-999/" not in p for p in deleted)
    assert deleted == [f"{ROOT}/M-1A/M-1A.json", f"{ROOT}/M-1A/M-1A.iso.xml"]


def test_apply_refuses_when_too_many_renames(monkeypatch):
    """A source that renamed most of the catalog at once is more likely broken than renamed."""
    orphans = {f"{ROOT}/M-{n}X": f"https://ugspub.nr.utah.gov/publications/maps/m-{n}.pdf" for n in range(50)}
    deleted = _catalog(monkeypatch, orphans, SOURCE)
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
