"""Comment generation + freeze logic — the pure pieces (no DB). The generation column, composite
item_status key, and the write-freeze/history-read wiring run against Postgres on the deploy; here we
cover the read_only classifier and the constants that drive it."""
from ugs_warehouse import comments


def test_generations_and_statuses():
    assert comments.GENERATIONS == ("review", "published")
    assert "promoted" in comments.ITEM_STATUSES  # the freeze trigger for a review thread


def test_is_read_only_only_frozen_review_threads():
    promoted = {"wells"}  # wells' review generation was promoted → its review thread is history

    # Review comment on a promoted layer → frozen history, read-only.
    assert comments._is_read_only({"generation": "review", "item_ids": ["wells"]}, promoted) is True
    # Published comment on the same layer → the live prod thread, never read-only.
    assert comments._is_read_only({"generation": "published", "item_ids": ["wells"]}, promoted) is False
    # Review comment on a layer that hasn't been promoted → still active, writable.
    assert comments._is_read_only({"generation": "review", "item_ids": ["soils"]}, promoted) is False
    # Multi-item review comment touching any promoted layer → frozen.
    assert comments._is_read_only({"generation": "review", "item_ids": ["soils", "wells"]}, promoted) is True
    # No promoted layers at all → nothing frozen.
    assert comments._is_read_only({"generation": "review", "item_ids": ["wells"]}, set()) is False


def test_is_read_only_defaults_are_safe():
    # Missing/empty fields must not crash and must not falsely freeze.
    assert comments._is_read_only({}, {"wells"}) is False
    assert comments._is_read_only({"generation": "review", "item_ids": []}, {"wells"}) is False
