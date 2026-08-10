"""Which SPA shell a client-side route falls back to, and which bucket serves it (serve.py).

The review service hosts two SPAs out of the review bucket — the hazards-review app and the
internal viewer — plus per-PR previews, which live in a SEPARATE bucket so the identity that
writes them needs no access to review data at all.

Serving the wrong index.html doesn't error: the browser renders a DIFFERENT BUILD than the URL
promises. Reading the wrong bucket doesn't error either, it just 404s. Both are pinned here.
"""
from __future__ import annotations

import pytest

serve = pytest.importorskip("ugs_warehouse.serve")

APP = serve.APP_PREFIX
VIEWER = serve.VIEWER_PREFIX
PREVIEW = serve.PREVIEW_PREFIX


@pytest.mark.parametrize("path, expected", [
    (f"{APP}/pr-154/assets/x.js", f"{APP}/pr-154/index.html"),
    (f"{APP}/pr-154/", f"{APP}/pr-154/index.html"),
    (f"{APP}/pr-154", f"{APP}/pr-154/index.html"),
    (f"{PREVIEW}/viewer/pr-154/deep/route", f"{PREVIEW}/viewer/pr-154/index.html"),
    (f"{PREVIEW}/viewer/pr-154", f"{PREVIEW}/viewer/pr-154/index.html"),
    (f"{PREVIEW}/app/pr-7/x", f"{PREVIEW}/app/pr-7/index.html"),
])
def test_a_preview_serves_its_own_shell(path, expected):
    assert serve._spa_index_for(path) == expected


@pytest.mark.parametrize("path, expected", [
    (f"{APP}/", f"{APP}/index.html"),
    (f"{APP}/some/route", f"{APP}/index.html"),
    (f"{VIEWER}/", serve.VIEWER_INDEX),
    (f"{VIEWER}/some/route", serve.VIEWER_INDEX),
    ("", serve.VIEWER_INDEX),
    ("anything/else", serve.VIEWER_INDEX),
])
def test_live_routes_are_unchanged(path, expected):
    assert serve._spa_index_for(path) == expected


def test_a_segment_that_merely_starts_with_pr_is_not_a_preview():
    # `pr-` is a path SEGMENT, not a substring: `.../prod/` and `.../previews/` must not match.
    assert serve._spa_index_for(f"{VIEWER}/prod/x.js") == serve.VIEWER_INDEX
    assert serve._spa_index_for(f"{APP}/previews/x.js") == f"{APP}/index.html"


# --- which bucket answers -----------------------------------------------------------------------

def test_previews_read_the_preview_bucket_and_nothing_else_does(monkeypatch):
    """The privilege boundary is the bucket, so the routing that picks it is worth asserting."""
    sentinel = object()
    monkeypatch.setattr(serve, "_preview_store", sentinel)

    assert serve._store_for(f"{PREVIEW}/viewer/pr-154/index.html") is sentinel
    assert serve._store_for(PREVIEW) is sentinel
    # Everything live stays on the review bucket.
    for path in (f"{VIEWER}/index.html", f"{APP}/index.html", "review/stac/catalog.json", ""):
        assert serve._store_for(path) is serve._store, path
    # A prefix that merely starts with the same letters is not the preview subtree.
    assert serve._store_for(f"{PREVIEW}s/viewer/x") is serve._store


def test_without_a_preview_bucket_configured_everything_uses_the_review_bucket(monkeypatch):
    monkeypatch.setattr(serve, "_preview_store", None)
    assert serve._store_for(f"{PREVIEW}/viewer/pr-154/index.html") is serve._store
