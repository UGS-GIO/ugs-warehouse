"""Which SPA shell a client-side route falls back to (serve.py).

The review service hosts two SPAs out of one bucket — the hazards-review app and the internal
viewer — plus a per-PR preview subtree of either. Serving the wrong index.html doesn't error: the
browser renders a DIFFERENT BUILD than the URL promises, which is the failure mode worth pinning.
"""
from __future__ import annotations

import pytest

serve = pytest.importorskip("ugs_warehouse.serve")

APP = serve.APP_PREFIX
VIEWER = serve.VIEWER_PREFIX


@pytest.mark.parametrize("path, expected", [
    (f"{APP}/pr-154/assets/x.js", f"{APP}/pr-154/index.html"),
    (f"{APP}/pr-154/", f"{APP}/pr-154/index.html"),
    (f"{APP}/pr-154", f"{APP}/pr-154/index.html"),
    # The gap this closes: viewer previews resolved to the LIVE viewer shell, so a reviewer
    # clicking a preview link saw main's build with no indication anything was wrong.
    (f"{VIEWER}/pr-154/deep/route", f"{VIEWER}/pr-154/index.html"),
    (f"{VIEWER}/pr-154", f"{VIEWER}/pr-154/index.html"),
])
def test_a_preview_serves_its_own_shell(path, expected):
    assert serve._spa_index_for(path) == expected


@pytest.mark.parametrize("path, expected", [
    (f"{APP}/", f"{APP}/index.html"),
    (f"{APP}/some/route", f"{APP}/index.html"),
    (f"{VIEWER}/", serve.VIEWER_INDEX),
    (f"{VIEWER}/some/route", serve.VIEWER_INDEX),
    ("", serve.VIEWER_INDEX),                      # root → the internal viewer
    ("anything/else", serve.VIEWER_INDEX),
])
def test_live_routes_are_unchanged(path, expected):
    assert serve._spa_index_for(path) == expected


def test_a_prefix_that_merely_starts_with_pr_is_not_a_preview():
    # `pr-` is a path SEGMENT, not a substring: `.../previews/` or `.../prod/` must not match.
    assert serve._spa_index_for(f"{VIEWER}/prod/x.js") == serve.VIEWER_INDEX
    assert serve._spa_index_for(f"{APP}/previews/x.js") == f"{APP}/index.html"
