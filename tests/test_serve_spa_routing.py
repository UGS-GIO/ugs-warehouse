"""Which SPA shell a client-side route falls back to, in both of serve.py's deployments.

The same image runs twice. On the REVIEW service it hosts the review app, the internal viewer, the
review app's own PR previews, and the /api/* routes. On the PREVIEWS service (`REVIEW_STATIC_ONLY`)
it hosts per-PR bundles and nothing else — a separate origin, so preview JavaScript (unmerged
branch code a reviewer is invited to load) cannot act as that reviewer against the review API.

Serving the wrong index.html doesn't error, it renders a different build than the URL promises.
Mounting /api on the previews origin doesn't error either, it just quietly undoes the isolation.
Both are pinned here.
"""
from __future__ import annotations

import importlib

import pytest

serve = pytest.importorskip("ugs_warehouse.serve")


@pytest.fixture
def previews(monkeypatch):
    """serve.py as the previews deployment. Reimported so module-level flags are re-read."""
    monkeypatch.setenv("REVIEW_STATIC_ONLY", "1")
    monkeypatch.setenv("WAREHOUSE_BUCKET", "ut-dnr-ugs-maps-prod-previews")
    module = importlib.reload(serve)
    yield module
    monkeypatch.delenv("REVIEW_STATIC_ONLY", raising=False)
    importlib.reload(serve)          # leave the review-mode module in place for other tests


# --- the review service (default) ---------------------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    (f"{serve.APP_PREFIX}/pr-154/assets/x.js", f"{serve.APP_PREFIX}/pr-154/index.html"),
    (f"{serve.APP_PREFIX}/pr-154/", f"{serve.APP_PREFIX}/pr-154/index.html"),
    (f"{serve.APP_PREFIX}/pr-154", f"{serve.APP_PREFIX}/pr-154/index.html"),
])
def test_the_review_app_preview_serves_its_own_shell(path, expected):
    assert serve._spa_index_for(path) == expected


@pytest.mark.parametrize("path", [
    f"{serve.APP_PREFIX}/", f"{serve.APP_PREFIX}/some/route",
    f"{serve.VIEWER_PREFIX}/", f"{serve.VIEWER_PREFIX}/some/route", "", "anything/else",
])
def test_live_routes_still_resolve_to_a_live_shell(path):
    assert serve._spa_index_for(path).endswith("index.html")


def test_a_segment_that_merely_starts_with_pr_is_not_a_preview():
    # `pr-` is a path SEGMENT, not a substring: `.../prod/` and `.../previews/` must not match.
    assert serve._spa_index_for(f"{serve.VIEWER_PREFIX}/prod/x.js") == serve.VIEWER_INDEX
    assert serve._spa_index_for(f"{serve.APP_PREFIX}/previews/x.js") == f"{serve.APP_PREFIX}/index.html"


def test_the_review_service_serves_the_api():
    # Via the OpenAPI schema, not app.routes: FastAPI wraps included routers in an opaque object
    # with no `.path`, so walking routes silently reports no /api at all.
    assert "/api/comments" in serve.app.openapi()["paths"]


# --- the previews service (REVIEW_STATIC_ONLY) ---------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    ("viewer/pr-154/assets/x.js", "viewer/pr-154/index.html"),
    ("viewer/pr-154/deep/route", "viewer/pr-154/index.html"),
    ("viewer/pr-154", "viewer/pr-154/index.html"),
    ("app/pr-7/x", "app/pr-7/index.html"),
])
def test_every_preview_serves_its_own_shell(previews, path, expected):
    assert previews._spa_index_for(path) == expected


@pytest.mark.parametrize("path", ["", "anything", "review/app/x", "viewer/prod/x.js"])
def test_a_non_preview_path_is_404_not_someone_elses_app(previews, path):
    # The failure this prevents: falling back to a live shell on an origin that has no live app.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        previews._spa_index_for(path)
    assert exc.value.status_code == 404


def test_the_previews_service_hosts_no_api_at_all(previews):
    """The origin split is the security boundary; mounting /api here would erase it."""
    api = [p for p in previews.app.openapi()["paths"] if p.startswith("/api")]
    assert not api, api
    # …but it still serves objects.
    assert "/{object_path:path}" in {getattr(r, "path", "") for r in previews.app.routes}
