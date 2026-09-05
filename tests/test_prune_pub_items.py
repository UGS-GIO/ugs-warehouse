"""Hermetic tests for scripts/prune_pub_items.py."""
from __future__ import annotations

from unittest.mock import patch

from scripts.prune_pub_items import expected_pub_items, orphan_pub_paths


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
