"""Upsert a PR comment linking to a per-PR preview (#161/#163).

Cloud Build's own GitHub check only deep-links to the Cloud Build console — a reviewer had to dig
through console logs to find the one thing they actually need. This posts the link where it's
clickable. Marker-tagged and idempotent: a force-push updates the same comment via PATCH instead of
piling up a new one per push.

Called from cloudbuild-viewer-preview.yaml as a standalone script rather than an inline heredoc —
embedding a Python heredoc inside a YAML block-scalar bash step doesn't work: the `<<'EOF'` closing
line inherits the step's YAML indentation, so it never matches bash's exact (unindented) terminator
and the heredoc never closes. Real failure mode hit shipping this the first time: the step "passed"
with zero output because bash fed the rest of the step (including the literal EOF marker) to Python
as one blob, which errored, but the whole thing was `|| true`-guarded into silence.

Usage: PR_NUMBER, PREVIEW_URL, GITHUB_TOKEN, REPO (owner/name), MARKER env vars set; run standalone.
Exits 0 always — a comment failure must never fail a build that already deployed (same reasoning as
the `|| true` at the call site; this is the belt to that suspenders).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    repo = os.environ["REPO"]
    pr = os.environ["PR_NUMBER"]
    preview_url = os.environ["PREVIEW_URL"]
    token = os.environ["GITHUB_TOKEN"]
    marker = os.environ.get("MARKER", "<!-- ugs-warehouse-preview -->")

    body = f"{marker}\n\U0001f517 **Preview:** {preview_url}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    def req(method: str, path: str, data: dict | None = None):
        r = urllib.request.Request(
            f"https://api.github.com{path}",
            method=method,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.load(resp)

    existing = req("GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100")
    mine = next((c for c in existing if marker in c.get("body", "")), None)
    if mine:
        req("PATCH", f"/repos/{repo}/issues/comments/{mine['id']}", {"body": body})
    else:
        req("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - best-effort, never fail the build over this
        print(f"post_preview_comment: non-fatal: {exc}", file=sys.stderr)
        sys.exit(0)
