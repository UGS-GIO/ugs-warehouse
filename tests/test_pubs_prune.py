"""Hermetic tests for ugs_warehouse.pubs.prune."""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from ugs_warehouse.core import config
from ugs_warehouse.pubs import prune as P

PFX = config.STAC_PREFIX


def _listing(monkeypatch, listing: list[str]):
    monkeypatch.setattr(P.gcs, "list_paths", lambda root: [p for p in listing if p.startswith(root)])


def test_expected_pub_dirs():
    assert P.expected_pub_dirs([{"series_id": "OFR-1"}, {"series_id": "MD-50"}, {"series_id": " "}]) == {
        "ugs-publications/OFR/OFR-1", "ugs-mining-district-files/MD/MD-50"}


def test_orphans_are_whole_publication_dirs_including_nested_items(monkeypatch):
    _listing(monkeypatch, [
        f"{PFX}/ugs-publications/OFR/OFR-1/collection.json",
        f"{PFX}/ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
        f"{PFX}/ugs-publications/OFR/ofr-1/collection.json",
        f"{PFX}/ugs-publications/OFR/ofr-1/ofr-1_plate/ofr-1_plate.json",
        f"{PFX}/ugs-publications/OFR/catalog.json",
    ])
    orphans = P.orphan_pub_paths(expected={"ugs-publications/OFR/OFR-1"}, groups=("ugs-publications",))
    assert sorted(orphans) == [f"{PFX}/ugs-publications/OFR/ofr-1/collection.json",
                               f"{PFX}/ugs-publications/OFR/ofr-1/ofr-1_plate/ofr-1_plate.json"]


def test_legacy_layout_files_inside_expected_pubs_and_series(monkeypatch):
    _listing(monkeypatch, [
        f"{PFX}/ugs-publications/OFR/OFR-1/OFR-1.json",
        f"{PFX}/ugs-publications/OFR/OFR-1/OFR-1.iso.xml",
        f"{PFX}/ugs-publications/OFR/OFR-1/collection.json",
        f"{PFX}/ugs-publications/OFR/collection.json",
        f"{PFX}/ugs-publications/OFR/items.json",
        f"{PFX}/ugs-publications/OFR/catalog.json",
    ])
    legacy = P.legacy_layout_paths(expected={"ugs-publications/OFR/OFR-1"}, groups=("ugs-publications",))
    # The pub ISO sidecar is the new Collection's record, so it is never legacy.
    assert sorted(legacy) == sorted([f"{PFX}/ugs-publications/OFR/OFR-1/OFR-1.json",
                                     f"{PFX}/ugs-publications/OFR/collection.json",
                                     f"{PFX}/ugs-publications/OFR/items.json"])


def test_legacy_keeps_an_old_item_with_no_new_collection(monkeypatch):
    _listing(monkeypatch, [f"{PFX}/ugs-publications/OFR/OFR-1/OFR-1.json",
                           f"{PFX}/ugs-publications/OFR/catalog.json"])
    assert P.legacy_layout_paths(expected={"ugs-publications/OFR/OFR-1"}, groups=("ugs-publications",)) == []


def test_legacy_keeps_series_files_with_no_new_catalog(monkeypatch):
    _listing(monkeypatch, [f"{PFX}/ugs-publications/OFR/collection.json",
                           f"{PFX}/ugs-publications/OFR/items.json"])
    assert P.legacy_layout_paths(expected=set(), groups=("ugs-publications",)) == []


def test_renamed_reads_collection_json_then_the_old_item(monkeypatch):
    docs = {"g/S/d1/collection.json": {"assets": {"publication": {"href": "a.pdf"}}},
            "g/S/d2/d2.json": {"assets": {"publication": {"href": "b.pdf"}}}}

    def get_bytes(path):
        if path not in docs:
            raise FileNotFoundError(path)
        return json.dumps(docs[path]).encode()

    monkeypatch.setattr(P.gcs, "get_bytes", get_bytes)
    assert P.renamed({"g/S/d1", "g/S/d2", "g/S/d3"}, {"a.pdf", "b.pdf"}) == {"g/S/d1", "g/S/d2"}


def _catalog(monkeypatch, orphans: dict[str, str], pubs: list[dict]):
    """Orphan pub dirs -> their publication file; the source lists `pubs`. Returns the deleted list."""
    paths = [p for d in orphans for p in (f"{d}/collection.json", f"{d}/{d.rsplit('/', 1)[1]}_plate/x.json")]
    docs = {f"{d}/collection.json": json.dumps({"assets": {"publication": {"href": f}}}).encode()
            for d, f in orphans.items()}
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    monkeypatch.setattr(P.gcs, "get_bytes", lambda path: docs[path])
    monkeypatch.setattr(P.source, "read_pubs", lambda: pubs)
    monkeypatch.setattr(P.source, "read_attachments", lambda: [])
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: list(paths))
    return deleted


ROOT = f"{PFX}/ugs-publications/OFR"
SOURCE = [{"series_id": f"OFR-{n}", "pub_url": f"ofr/ofr-{n}.pdf"} for n in range(100)]
FILE1 = "https://ugspub.nr.utah.gov/publications/ofr/ofr-1.pdf"


def test_dry_run_deletes_nothing_and_apply_deletes_a_renamed_pub_dir(monkeypatch):
    """The assertion that matters most for a job whose whole job is deleting objects: without
    --apply it reports and touches nothing."""
    deleted = _catalog(monkeypatch, {f"{ROOT}/OFR-1A": FILE1}, SOURCE)
    monkeypatch.setattr(sys, "argv", ["prune"])
    assert P.main() == 0
    assert deleted == []

    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 0
    assert deleted == [f"{ROOT}/OFR-1A/collection.json", f"{ROOT}/OFR-1A/OFR-1A_plate/x.json"]


def test_an_orphan_whose_file_the_source_lacks_is_kept(monkeypatch):
    """A document the source does not list is missing from the source, not a stale copy."""
    deleted = _catalog(monkeypatch, {
        f"{ROOT}/OFR-1A": FILE1,
        f"{ROOT}/OFR-999": "https://ugspub.nr.utah.gov/publications/ofr/legacy-scan.pdf"}, SOURCE)
    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 0
    assert all("/OFR-999/" not in p for p in deleted)
    assert deleted == [f"{ROOT}/OFR-1A/collection.json", f"{ROOT}/OFR-1A/OFR-1A_plate/x.json"]


def test_apply_refuses_when_too_many_renames(monkeypatch):
    """A source that renamed most of the catalog at once is more likely broken than renamed."""
    orphans = {f"{ROOT}/OFR-{n}X": f"https://ugspub.nr.utah.gov/publications/ofr/ofr-{n}.pdf"
               for n in range(50)}
    deleted = _catalog(monkeypatch, orphans, SOURCE)
    monkeypatch.setattr(sys, "argv", ["prune", "--apply"])
    assert P.main() == 1
    assert deleted == []


def test_legacy_flag_lists_in_dry_run_and_deletes_with_apply(monkeypatch, capsys):
    legacy = [f"{ROOT}/OFR-1/OFR-1.json", f"{ROOT}/collection.json"]
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    monkeypatch.setattr(P.source, "read_pubs", lambda: SOURCE)
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: [])
    monkeypatch.setattr(P, "_legacy_scan", lambda expected, groups: (list(legacy), 2))

    monkeypatch.setattr(sys, "argv", ["prune", "--legacy"])
    assert P.main() == 0
    out = capsys.readouterr().out
    assert deleted == []
    assert "2 file(s) from the old layout" in out
    assert f"would delete {legacy[0]}" in out

    monkeypatch.setattr(sys, "argv", ["prune", "--legacy", "--apply"])
    assert P.main() == 0
    assert deleted == legacy


def test_legacy_apply_refuses_past_the_bound(monkeypatch, capsys):
    legacy = [f"{ROOT}/x{n}.json" for n in range(100 + 3 * 1 + 1)]
    deleted: list[str] = []
    monkeypatch.setattr(P.gcs, "delete", deleted.append)
    monkeypatch.setattr(P.source, "read_pubs", lambda: SOURCE)
    monkeypatch.setattr(P, "orphan_pub_paths", lambda expected=None: [])
    monkeypatch.setattr(P, "_legacy_scan", lambda expected, groups: (list(legacy), 1))

    monkeypatch.setattr(sys, "argv", ["prune", "--legacy"])
    assert P.main() == 0
    assert "would REFUSE" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["prune", "--legacy", "--apply"])
    assert P.main() == 1
    assert deleted == []


def test_orphan_refusal_stops_before_the_legacy_block(monkeypatch):
    orphans = {f"{ROOT}/OFR-{n}X": f"https://ugspub.nr.utah.gov/publications/ofr/ofr-{n}.pdf"
               for n in range(50)}
    deleted = _catalog(monkeypatch, orphans, SOURCE)
    monkeypatch.setattr(P, "_legacy_scan", lambda expected, groups: pytest.fail("legacy ran"))
    monkeypatch.setattr(sys, "argv", ["prune", "--legacy", "--apply"])
    assert P.main() == 1
    assert deleted == []


def test_a_case_twin_goes_and_the_correct_spelling_stays(monkeypatch):
    """The bug this script exists for: two ids differing only in case, one of them legitimate.
    The comparison has to be against what the source actually produces."""
    expected = P.expected_pub_dirs([{"series_id": "MUS-Keetley"}, {"series_id": "MD-50"}])
    _listing(monkeypatch, [
        f"{PFX}/ugs-publications/MUS/MUS-Keetley/collection.json",
        f"{PFX}/ugs-publications/MUS/MUS-KEETLEY/collection.json",
        f"{PFX}/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY_plate/MUS-KEETLEY_plate.json",
        f"{PFX}/ugs-mining-district-files/MD/MD-50/collection.json",
    ])
    assert P.orphan_pub_paths(expected=expected) == [
        f"{PFX}/ugs-publications/MUS/MUS-KEETLEY/collection.json",
        f"{PFX}/ugs-publications/MUS/MUS-KEETLEY/MUS-KEETLEY_plate/MUS-KEETLEY_plate.json"]


def test_the_scan_never_leaves_the_publication_prefixes():
    """PUB_GROUPS is the only thing between this script and the serving topics."""
    seen: list[str] = []

    def record(prefix: str) -> list[str]:
        seen.append(prefix)
        return []

    with patch("ugs_warehouse.core.gcs.list_paths", side_effect=record):
        P.orphan_pub_paths(expected=set())
        P.legacy_layout_paths(expected=set())

    groups = [f"{PFX}/{g}/" for g in P.PUB_GROUPS]
    assert seen == groups + groups
    assert not any("serving-topics" in p or "ugs-rasters" in p for p in seen)


def test_stale_item_dirs_are_removed_and_the_collection_files_kept(monkeypatch, capsys):
    from ugs_warehouse.pubs import ingest

    base = f"{PFX}/ugs-publications/OFR/OFR-1"
    listing = [f"{base}/collection.json", f"{base}/README.md", f"{base}/AGENTS.md", f"{base}/OFR-1.iso.xml",
               f"{base}/OFR-1_plate/OFR-1_plate.json", f"{base}/OFR-1_x/OFR-1_x.json"]
    deleted: list[str] = []
    monkeypatch.setattr(ingest.gcs, "list_paths", lambda root: [p for p in listing if p.startswith(root)])
    monkeypatch.setattr(ingest.gcs, "delete", deleted.append)
    ingest._prune_stale_items("ugs-publications/OFR/OFR-1", {"OFR-1_x"})
    assert deleted == [f"{base}/OFR-1_plate/OFR-1_plate.json"]
    assert "removed stale item ugs-publications/OFR/OFR-1/OFR-1_plate" in capsys.readouterr().err
    deleted.clear()
    ingest._prune_stale_items("ugs-publications/OFR/OFR-1", set())  # now single-file: no item dirs at all
    assert sorted(deleted) == [f"{base}/OFR-1_plate/OFR-1_plate.json", f"{base}/OFR-1_x/OFR-1_x.json"]
