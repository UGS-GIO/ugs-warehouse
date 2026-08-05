"""Prune of pre-split flat serving-topic objects — path selection only (no GCS, no deletes)."""
from scripts import prune_flat_topic_items as P
from ugs_warehouse.core import config


def test_selects_flat_objects_and_spares_the_nested_layout(monkeypatch):
    root = f"{config.STAC_PREFIX}/{P.CATALOG}"
    paths = [
        # pre-split flat layout — these go
        f"{root}/hazards_qfaults/hazards_qfaults.json",
        f"{root}/hazards_qfaults/hazards_qfaults.iso.xml",
        f"{root}/collection.json",
        # post-split nested layout — these stay
        f"{root}/hazards/hazards_qfaults/hazards_qfaults.json",
        f"{root}/hazards/hazards_qfaults/hazards_qfaults.iso.xml",
        f"{root}/hazards/collection.json",
        f"{root}/hazards/items.json",
        f"{root}/catalog.json",
        f"{root}/items.json",
    ]
    monkeypatch.setattr(P.gcs, "list_paths", lambda pre: [p for p in paths if p.startswith(pre)])

    flat, docs = P._stale_paths()
    assert flat == [f"{root}/hazards_qfaults/hazards_qfaults.json",
                    f"{root}/hazards_qfaults/hazards_qfaults.iso.xml"]
    assert docs == [f"{root}/collection.json"]
