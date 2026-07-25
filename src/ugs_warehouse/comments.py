"""Review comments API — reviewers annotate 1..N review items with notes/feedback.

Served by the IAP review app (serve.py), stored in `review.comments` in mapping-db, connected as the
least-privilege `review_writer` role (owns the `review` schema, can touch nothing else). The author of
every comment is the IAP identity (X-Goog-Authenticated-User-Email), never trusted from the client.
Reports read this table via DuckDB later.

DB config comes from the same Cloud SQL socket the ingest uses (`--set-cloudsql-instances` mounts it at
/cloudsql/<instance>); the serving deploy sets DB_USER=review_writer + DB_PASS from Secret Manager.
Everything is best-effort: if the DB isn't wired yet, the app still serves files — the comment routes
just return 503.
"""
from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger("ugs-warehouse.comments")
router = APIRouter(prefix="/api/comments", tags=["comments"])

_pool = None  # asyncpg pool, lazily created


def _conn_kwargs() -> dict:
    """asyncpg connection args. Prefers a full URI in REVIEW_DB_DSN; else the Cloud SQL unix socket the
    deploy mounts (`--set-cloudsql-instances`), as review_writer with the secret password."""
    if os.environ.get("REVIEW_DB_DSN"):
        return {"dsn": os.environ["REVIEW_DB_DSN"]}
    inst = os.environ.get("CLOUDSQL_INSTANCE")
    if not inst:
        raise HTTPException(status_code=503, detail="comments DB not configured")
    return {
        "host": f"/cloudsql/{inst}",  # asyncpg reads the socket in this dir
        "database": os.environ.get("DB_NAME", "seamlessgeolmap"),
        "user": os.environ.get("DB_USER", "review_writer"),
        "password": os.environ.get("DB_PASS", ""),
    }


# A comment targets 1..N items, and optionally 1..N rows or a column of those items.
# target_kind = item | row | column. A ROW comment is keyed on a STABLE domain key — `row_key` is the
# column name (e.g. 'pk'), `row_key_vals` the values — NOT the ephemeral feature_id (a Hilbert
# row-number that reshuffles on re-ingest and differs between the internal viewer and the map viewer).
# The stable key is identical across the parquet, PMTiles, and PostGIS, so a comment made in either app
# resolves to the same row in the other. The old feature_ids column is retired (dropped below).
_DDL = """
    CREATE TABLE IF NOT EXISTS review.comments (
      id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      item_ids     TEXT[]      NOT NULL,
      generation   TEXT        NOT NULL DEFAULT 'review',  -- review | published (which lifecycle stage)
      target_kind  TEXT        NOT NULL DEFAULT 'item',
      row_key      TEXT,                 -- the stable-key COLUMN name for a row comment (e.g. 'pk')
      row_key_vals TEXT[],               -- 1..N stable key values (target_kind = row)
      column_name  TEXT,
      parent_id    BIGINT,
      body         TEXT        NOT NULL,
      author       TEXT        NOT NULL,
      status       TEXT        NOT NULL DEFAULT 'open',
      created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS target_kind  TEXT NOT NULL DEFAULT 'item';
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS column_name  TEXT;
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS parent_id    BIGINT;
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS row_key      TEXT;
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS row_key_vals TEXT[];
    -- `generation` splits a layer's comment threads by lifecycle stage: 'review' (pre-release) vs
    -- 'published' (the promoted prod layer). Same STAC id, different thread → prod and review comments
    -- never collide. Existing rows are review-time, so the default backfills them correctly.
    ALTER TABLE review.comments ADD COLUMN IF NOT EXISTS generation   TEXT NOT NULL DEFAULT 'review';
    -- Retire the ephemeral feature id(s) in favour of the stable domain key (warehouse not prod;
    -- existing row targets are dropped — item/column comments are untouched).
    ALTER TABLE review.comments DROP COLUMN IF EXISTS feature_ids;
    ALTER TABLE review.comments DROP COLUMN IF EXISTS feature_id;
    CREATE INDEX IF NOT EXISTS comments_items_gin ON review.comments USING GIN (item_ids);
    CREATE INDEX IF NOT EXISTS comments_rowvals_gin ON review.comments USING GIN (row_key_vals);
    CREATE INDEX IF NOT EXISTS comments_parent ON review.comments (parent_id);
    CREATE INDEX IF NOT EXISTS comments_generation ON review.comments (generation);

    -- Per-layer review status — tracks completion of a layer's review, independent of whether every
    -- comment is resolved. `approved` is the "ready" state that feeds the review→current promotion;
    -- `promoted` (set on the 'review' generation once published) FREEZES that review thread to read-only
    -- history. Keyed on (item_id, generation) so a layer's review and published states are independent.
    CREATE TABLE IF NOT EXISTS review.item_status (
      item_id    TEXT        NOT NULL,
      generation TEXT        NOT NULL DEFAULT 'review',
      status     TEXT        NOT NULL DEFAULT 'pending',
      updated_by TEXT        NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    -- Migrate a pre-existing single-column-PK item_status to the composite key (idempotent).
    ALTER TABLE review.item_status ADD COLUMN IF NOT EXISTS generation TEXT NOT NULL DEFAULT 'review';
    -- Drop whatever PRIMARY KEY exists by looking its name up in the catalog — never a hard-coded name.
    -- The old single-column PK (on item_id) is auto-named, and if a wrong name were assumed the DROP
    -- would silently no-op, leaving item_id unique → a second generation's INSERT would fail. This drops
    -- the real one regardless of name, and no-ops once it's gone (idempotent under the self-healing DDL).
    DO $$
    DECLARE pk_name text;
    BEGIN
      SELECT conname INTO pk_name FROM pg_constraint
      WHERE conrelid = 'review.item_status'::regclass AND contype = 'p';
      IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE review.item_status DROP CONSTRAINT %I', pk_name);
      END IF;
    END $$;
    CREATE UNIQUE INDEX IF NOT EXISTS item_status_gen_uq ON review.item_status (item_id, generation);

    -- In-app notifications: a new comment fans out to the people it @mentions and (for a reply) the
    -- other participants in the thread. Fully internal — no email/chat. Cascades with the comment.
    CREATE TABLE IF NOT EXISTS review.notifications (
      id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      recipient   TEXT        NOT NULL,   -- IAP email being notified
      actor       TEXT        NOT NULL,   -- who triggered it (the comment author)
      comment_id  BIGINT      NOT NULL REFERENCES review.comments(id) ON DELETE CASCADE,
      kind        TEXT        NOT NULL,   -- mention | reply
      seen_at     TIMESTAMPTZ,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS notifications_recipient ON review.notifications (recipient, created_at DESC);
    CREATE INDEX IF NOT EXISTS notifications_comment ON review.notifications (comment_id);

    -- Reviewer directory: every IAP identity that touches the review app, auto-provisioned on each
    -- authed request (the UCRC pattern — IAP gates the whole utah.gov domain, so the actual reviewer
    -- roster is "whoever has used the app"). This is the @-mention autocomplete list + the
    -- notification-recipient source: no Cloud Identity group (org-policy-blocked) and no env list.
    CREATE TABLE IF NOT EXISTS review.reviewers (
      email      TEXT PRIMARY KEY,
      first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""

# The layer review lifecycle. `approved` = ready to promote (ingest#190 R→Y); `promoted` = published,
# which freezes the review-generation comments to read-only history.
ITEM_STATUSES = ("pending", "in_review", "changes_requested", "approved", "promoted")

# A comment/status belongs to one lifecycle generation of a layer. Same STAC id, separate threads.
GENERATIONS = ("review", "published")


async def _get_pool():
    """Lazily create the asyncpg pool AND ensure the table exists (review_writer owns the `review`
    schema, so it can DDL). Self-healing: the Cloud SQL socket isn't up at container boot (Cloud Run
    sidecar race), so we DON'T rely on a one-shot startup call — the table gets created on the first
    successful pool connect. On any failure nothing is cached, so the next request retries fully."""
    global _pool
    if _pool is None:
        import asyncpg  # lazy — the driver isn't needed unless comments are used
        pool = await asyncpg.create_pool(**_conn_kwargs(), min_size=1, max_size=4)
        try:
            async with pool.acquire() as c:
                await c.execute(_DDL)
        except Exception:
            await pool.close()  # don't cache a pool whose schema didn't land — retry next request
            raise
        _pool = pool
        log.info("review.comments pool + schema ready")
    return _pool


async def init_schema() -> None:
    """Startup warm-up — best-effort; if the socket isn't ready yet, the first request self-heals."""
    await _get_pool()


# Firebase project that issues the hazards-review app's ID tokens (same GCP project as this service).
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "ut-dnr-ugs-maps-prod")
_fb_app = None  # lazily initialized firebase_admin app


def _bearer_token(request: Request) -> str | None:
    """The token from an `Authorization: Bearer <token>` header, or None."""
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else None


def _verify_firebase_email(token: str) -> str | None:
    """Verify a Firebase ID token (from the ugs-map-viewer /hazards-review app) and return the reviewer's
    email. Firebase re-issues its own signed JWT after the Entra OIDC exchange, so one verifier covers
    whichever upstream IdP the user came through. Returns None on any failure (never raises) — so a bad
    token just falls through to a 401, not a 500. Needs ADC (present on Cloud Run in the same project)."""
    global _fb_app
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth
        if _fb_app is None:
            _fb_app = firebase_admin.initialize_app(options={"projectId": FIREBASE_PROJECT_ID})
        claims = fb_auth.verify_id_token(token)
        # Prefer `email`; fall back to the Entra UPN claims if the email scope wasn't surfaced.
        email = claims.get("email") or claims.get("upn") or claims.get("preferred_username")
        return email if email else None
    except Exception:  # noqa: BLE001 — invalid/expired token, or firebase-admin not initializable
        log.warning("firebase token verification failed", exc_info=True)
        return None


def _author(request: Request) -> str:
    """The authenticated reviewer's email, from either trusted source (IAP first):
    1. IAP header `X-Goog-Authenticated-User-Email` — the internal review viewer (Google IAP).
    2. `Authorization: Bearer <firebase idToken>` — the hazards-review app in ugs-map-viewer (Firebase
       Auth / Entra OIDC). Emails match across both IdPs (confirmed), so the same person's rows line up.
    401 if neither is present/valid."""
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    email = raw.split(":", 1)[-1] if raw else ""
    if email:
        return email
    token = _bearer_token(request)
    if token and (email := _verify_firebase_email(token)):
        return email
    raise HTTPException(status_code=401, detail="no IAP identity or valid bearer token")


# WRITE authorization. READS (list comments/notifications, view catalog) are open to any authenticated
# user — everyone who can reach the hazards-review route may SEE review comments + data. WRITES
# (create/edit/delete a comment, set item status) are limited to authorized principals:
#   • an IAP-gated request is already restricted to the review group (the internal viewer) → trusted.
#   • a bearer request (public hazards-review app) must be in the editor allow-list below.
# Fail-CLOSED: if neither env is set, only IAP writers are allowed (bearer writes 403) — keep the list
# in sync with the review IAP/IAM principals. Cloud Identity group lookup is org-blocked, hence an
# explicit allow-list rather than a live membership check.
_EDITOR_EMAILS = {e.strip().lower() for e in os.environ.get("REVIEW_EDITOR_EMAILS", "").split(",") if e.strip()}
_EDITOR_DOMAINS = {d.strip().lower() for d in os.environ.get("REVIEW_EDITOR_DOMAINS", "").split(",") if d.strip()}


def _require_editor(request: Request) -> str:
    """The author email, but only if authorized to WRITE (see policy above). 401 if unauthenticated,
    403 if authenticated-but-not-an-editor."""
    email = _author(request)  # 401 if neither IAP identity nor a valid bearer
    if request.headers.get("x-goog-authenticated-user-email"):  # IAP-gated → already the review group
        return email
    lower = email.lower()
    domain = lower.rsplit("@", 1)[-1] if "@" in lower else ""
    if lower in _EDITOR_EMAILS or any(domain == d or domain.endswith("." + d) for d in _EDITOR_DOMAINS):
        return email
    raise HTTPException(status_code=403, detail="not authorized to edit review comments")


# Bounds keep a (trusted, IAP-gated) reviewer from storing a multi-MB body or a pathological array.
class NewComment(BaseModel):
    item_ids: list[str] = Field(default_factory=list, max_length=200)  # 1..N STAC item ids (required for a top-level comment)
    body: str = Field(max_length=20_000)
    generation: str = "review"                 # review | published — which lifecycle thread (a reply inherits its parent's)
    target_kind: str = "item"                  # item | row | column
    row_key: str | None = Field(default=None, max_length=128)  # stable-key column name (e.g. 'pk') for a row comment
    row_key_vals: list[str] | None = Field(default=None, max_length=10_000)  # 1..N stable key values
    column_name: str | None = Field(default=None, max_length=200)  # set when target_kind = column
    parent_id: int | None = None               # set on a reply; the reply inherits the parent's target


class PatchComment(BaseModel):
    body: str | None = Field(default=None, max_length=20_000)
    status: str | None = None  # open | resolved


# @token = the "@name" the composer inserts (localpart). Only matched at a word boundary, so the
# domain of a literal email in the body ("foo@bar.com") is NOT treated as a mention.
_MENTION_RE = re.compile(r"(?<!\S)@([A-Za-z0-9][A-Za-z0-9._%+-]*)")


def _mention_tokens(body: str) -> set[str]:
    """Lowercased @tokens in a comment body (e.g. {'alice', 'bob.smith'})."""
    return {m.group(1).lower() for m in _MENTION_RE.finditer(body or "")}


async def _touch_reviewer(pool, email: str) -> None:
    """Auto-provision the caller into the reviewer directory (UCRC pattern) — upsert on each authed
    request so the @-mention roster is 'everyone who has used the review app'. Best-effort: directory
    upkeep must never fail the actual request."""
    try:
        await pool.execute(
            "INSERT INTO review.reviewers (email) VALUES ($1) "
            "ON CONFLICT (email) DO UPDATE SET last_seen = now()", email)
    except Exception:  # noqa: BLE001
        log.exception("reviewer directory upsert failed for %s", email)


def _match_roster(emails: list[str], tokens: set[str]) -> set[str]:
    """Reviewer emails whose localpart (or full email), lowercased, is one of `tokens`."""
    return {e for e in emails
            if e.split("@", 1)[0].lower() in tokens or e.lower() in tokens}


async def _resolve_mentions(pool, tokens: set[str]) -> set[str]:
    """Resolve @tokens to reviewer emails via the auto-provisioned directory."""
    if not tokens:
        return set()
    rows = await pool.fetch("SELECT email FROM review.reviewers")
    return _match_roster([r["email"] for r in rows], tokens)


async def _emit_notifications(pool, comment: dict, author: str) -> None:
    """Fan a new comment out to in-app notifications: the reviewers it @mentions, plus (for a reply)
    the other participants already in the thread. Best-effort — a failure here must never fail the
    comment write. THIS is the single seam where a future Google Chat webhook would also fire.

    Both mention and reply recipients resolve from our own tables (the reviewer directory + the
    comments table), so notifications work immediately — no external roster dependency."""
    recipients: dict[str, str] = {}  # email -> kind; 'mention' outranks 'reply'

    # Reply: notify everyone already in the thread (root author + repliers), except the actor.
    if comment.get("parent_id"):
        root = comment["parent_id"]
        rows = await pool.fetch(
            "SELECT DISTINCT author FROM review.comments WHERE id = $1 OR parent_id = $1", root)
        for r in rows:
            if r["author"] != author:
                recipients[r["author"]] = "reply"

    # Mentions: resolve @tokens against the reviewer directory.
    for email in await _resolve_mentions(pool, _mention_tokens(comment["body"])):
        if email != author:
            recipients[email] = "mention"

    if not recipients:
        return
    await pool.executemany(
        "INSERT INTO review.notifications (recipient, actor, comment_id, kind) VALUES ($1, $2, $3, $4)",
        [(email, author, comment["id"], kind) for email, kind in recipients.items()],
    )


async def _promoted_review_items(pool) -> set[str]:
    """Item ids whose REVIEW generation has been promoted → their review threads are frozen (read-only
    history). One cheap query the list endpoint uses to flag every row."""
    rows = await pool.fetch(
        "SELECT item_id FROM review.item_status WHERE generation = 'review' AND status = 'promoted'")
    return {r["item_id"] for r in rows}


def _is_read_only(comment: dict, promoted: set[str]) -> bool:
    """A review-generation comment on a promoted layer is frozen history — read-only. Published
    comments are always live."""
    return comment.get("generation") == "review" and any(i in promoted for i in (comment.get("item_ids") or []))


async def _reject_if_frozen(pool, item_ids, generation: str) -> None:
    """Block a WRITE (new comment / edit / delete) that targets a frozen review thread — the layer was
    promoted, so its review comments are read-only history. Published threads are never frozen."""
    if generation != "review" or not item_ids:
        return
    if await _promoted_review_items(pool) & set(item_ids):
        raise HTTPException(status_code=409, detail="review thread is frozen (layer promoted); it is read-only history")


@router.post("")
async def create_comment(c: NewComment, request: Request) -> dict:
    if not c.body.strip():
        raise HTTPException(status_code=400, detail="body required")
    author = _require_editor(request)  # write → authorized principals only
    pool = await _get_pool()
    await _touch_reviewer(pool, author)  # keep the @-mention roster current

    if c.parent_id is not None:
        # A reply inherits the parent's target (item_ids/kind/row_key/column/generation) server-side, so
        # it can't be spoofed onto a different target and it matches every existing list filter. One level
        # of nesting only — replying to a reply attaches to the same top-level thread.
        parent = await pool.fetchrow("SELECT * FROM review.comments WHERE id = $1", c.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="parent comment not found")
        thread_root = parent["parent_id"] or parent["id"]
        item_ids, target_kind, generation = parent["item_ids"], parent["target_kind"], parent["generation"]
        row_key, row_key_vals, column_name = parent["row_key"], parent["row_key_vals"], parent["column_name"]
    else:
        if not c.item_ids:
            raise HTTPException(status_code=400, detail="item_ids required")
        if c.generation not in GENERATIONS:
            raise HTTPException(status_code=400, detail=f"generation must be one of {GENERATIONS}")
        if c.target_kind not in ("item", "row", "column"):
            raise HTTPException(status_code=400, detail="target_kind must be item|row|column")
        if c.target_kind == "row" and not (c.row_key and c.row_key_vals):
            raise HTTPException(status_code=400, detail="row comment needs row_key + row_key_vals")
        thread_root = None
        item_ids, target_kind, generation = c.item_ids, c.target_kind, c.generation
        row_key, row_key_vals, column_name = c.row_key, c.row_key_vals, c.column_name

    await _reject_if_frozen(pool, item_ids, generation)  # no new comments/replies on promoted-layer history

    row = await pool.fetchrow(
        "INSERT INTO review.comments (item_ids, generation, body, author, target_kind, row_key, row_key_vals, column_name, parent_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *",
        item_ids, generation, c.body.strip(), author, target_kind, row_key, row_key_vals, column_name, thread_root,
    )
    comment = dict(row)
    try:
        await _emit_notifications(pool, comment, author)
    except Exception:  # noqa: BLE001 — notifications are best-effort, never fail the comment write
        log.exception("notification emit failed for comment %s", comment.get("id"))
    return comment


@router.get("")
async def list_comments(
    request: Request,
    item_id: str | None = None,
    generation: str | None = None,  # 'review' | 'published' — omit to get BOTH threads (each flagged read_only)
    target_kind: str | None = None,  # 'item' | 'row' | 'column' — e.g. fetch just row comments for the orphan panel
    status: str | None = None,
    row_val: str | None = None,   # a stable row-key value → this row's comments (from either app)
    column: str | None = None,
) -> list[dict]:
    me = _author(request)  # require an authenticated session
    pool = await _get_pool()
    await _touch_reviewer(pool, me)  # opening any comment thread registers you in the roster
    where, args = ["TRUE"], []
    if item_id:
        args.append(item_id)
        where.append(f"${len(args)} = ANY(item_ids)")
    if generation is not None:
        args.append(generation)
        where.append(f"generation = ${len(args)}")
    if target_kind is not None:
        args.append(target_kind)
        where.append(f"target_kind = ${len(args)}")
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    if row_val is not None:
        args.append(row_val)
        where.append(f"${len(args)} = ANY(row_key_vals)")
    if column is not None:
        args.append(column)
        where.append(f"column_name = ${len(args)}")
    rows = await pool.fetch(
        f"SELECT * FROM review.comments WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 5000", *args
    )
    # Flag each comment read_only iff it's a review thread whose layer was promoted (frozen history), so
    # the app can render prod-active threads + review-history in one list without a second round-trip.
    promoted = await _promoted_review_items(pool)
    out = []
    for r in rows:
        d = dict(r)
        d["read_only"] = _is_read_only(d, promoted)
        out.append(d)
    return out


@router.patch("/{cid}")
async def patch_comment(cid: int, patch: PatchComment, request: Request) -> dict:
    author = _require_editor(request)  # write → authorized principals only
    pool = await _get_pool()
    cur = await pool.fetchrow("SELECT author, item_ids, generation FROM review.comments WHERE id = $1", cid)
    if not cur:
        raise HTTPException(status_code=404, detail="not found")
    await _reject_if_frozen(pool, cur["item_ids"], cur["generation"])  # frozen history is read-only
    sets, args = [], []
    if patch.body is not None:  # editing text is author-only
        if cur["author"] != author:
            raise HTTPException(status_code=403, detail="only the author can edit the body")
        args.append(patch.body.strip())
        sets.append(f"body = ${len(args)}")
    if patch.status is not None:  # resolving is open to any reviewer
        if patch.status not in ("open", "resolved"):
            raise HTTPException(status_code=400, detail="status must be open|resolved")
        args.append(patch.status)
        sets.append(f"status = ${len(args)}")
    if not sets:
        raise HTTPException(status_code=400, detail="nothing to update")
    args.append(cid)
    row = await pool.fetchrow(
        f"UPDATE review.comments SET {', '.join(sets)}, updated_at = now() WHERE id = ${len(args)} RETURNING *",
        *args,
    )
    return dict(row)


@router.delete("/{cid}")
async def delete_comment(cid: int, request: Request) -> dict:
    author = _require_editor(request)  # write → authorized principals only
    pool = await _get_pool()
    cur = await pool.fetchrow("SELECT author, item_ids, generation FROM review.comments WHERE id = $1", cid)
    if not cur:
        raise HTTPException(status_code=404, detail="not found")
    if cur["author"] != author:
        raise HTTPException(status_code=403, detail="only the author can delete")
    await _reject_if_frozen(pool, cur["item_ids"], cur["generation"])  # frozen history is read-only
    # Deleting a thread root removes its replies too (parent_id = cid).
    await pool.execute("DELETE FROM review.comments WHERE id = $1 OR parent_id = $1", cid)
    return {"deleted": cid}


# ---- Per-layer review status ----
status_router = APIRouter(prefix="/api/item-status", tags=["review-status"])


class SetStatus(BaseModel):
    status: str  # one of ITEM_STATUSES
    generation: str = "review"  # review | published — which lifecycle thread's status (defaults to review)


@status_router.get("")
async def list_item_status(
    request: Request, item_id: str | None = None, generation: str | None = None,
) -> list[dict]:
    """Every layer's review status (or one, if item_id given), optionally scoped to a generation. Items
    with no row are implicitly 'pending' — the client fills that in."""
    _author(request)
    pool = await _get_pool()
    where, args = ["TRUE"], []
    if item_id:
        args.append(item_id)
        where.append(f"item_id = ${len(args)}")
    if generation is not None:
        args.append(generation)
        where.append(f"generation = ${len(args)}")
    rows = await pool.fetch(
        f"SELECT * FROM review.item_status WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT 5000",
        *args)
    return [dict(r) for r in rows]


@status_router.put("/{item_id}")
async def set_item_status(item_id: str, s: SetStatus, request: Request) -> dict:
    if s.status not in ITEM_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {ITEM_STATUSES}")
    if s.generation not in GENERATIONS:
        raise HTTPException(status_code=400, detail=f"generation must be one of {GENERATIONS}")
    author = _require_editor(request)  # write → authorized principals only
    pool = await _get_pool()
    row = await pool.fetchrow(
        "INSERT INTO review.item_status (item_id, generation, status, updated_by, updated_at) "
        "VALUES ($1, $2, $3, $4, now()) "
        "ON CONFLICT (item_id, generation) DO UPDATE SET status = $3, updated_by = $4, updated_at = now() "
        "RETURNING *",
        item_id, s.generation, s.status, author,
    )
    return dict(row)


# ---- In-app notifications (mentions + thread replies) ----
notif_router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class SeenReq(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=5000)  # None = mark ALL of mine seen


@notif_router.get("")
async def list_notifications(request: Request, unseen: bool = False) -> list[dict]:
    """My notifications (newest first), each joined to its comment so the client can label + link it.
    `unseen=true` returns only the unread ones (used for the header badge count)."""
    me = _author(request)
    pool = await _get_pool()
    await _touch_reviewer(pool, me)  # the bell polls on every app load → registers each active reviewer
    where = "n.recipient = $1" + (" AND n.seen_at IS NULL" if unseen else "")
    rows = await pool.fetch(
        "SELECT n.id, n.actor, n.kind, n.seen_at, n.created_at, n.comment_id, "
        "       c.body, c.item_ids, c.generation, c.target_kind, c.row_key, c.row_key_vals, c.column_name, c.parent_id "
        "FROM review.notifications n JOIN review.comments c ON c.id = n.comment_id "
        f"WHERE {where} ORDER BY n.created_at DESC LIMIT 500",
        me,
    )
    return [dict(r) for r in rows]


@notif_router.post("/seen")
async def mark_seen(req: SeenReq, request: Request) -> dict:
    """Mark my notifications read. Body `{ids:[…]}` marks those; empty body marks all of mine."""
    me = _author(request)
    pool = await _get_pool()
    if req.ids:
        await pool.execute(
            "UPDATE review.notifications SET seen_at = now() "
            "WHERE recipient = $1 AND id = ANY($2) AND seen_at IS NULL",
            me, req.ids,
        )
    else:
        await pool.execute(
            "UPDATE review.notifications SET seen_at = now() WHERE recipient = $1 AND seen_at IS NULL",
            me,
        )
    return {"ok": True}


# ---- Reviewer directory (the @-mention roster) ----
reviewers_router = APIRouter(prefix="/api/reviewers", tags=["reviewers"])


@reviewers_router.get("")
async def list_reviewers(request: Request) -> list[str]:
    """Emails in the reviewer directory — everyone who has used the review app — for @-mention
    autocomplete. Auto-provisioned on each authed request (UCRC pattern); no external roster."""
    me = _author(request)
    pool = await _get_pool()
    await _touch_reviewer(pool, me)
    rows = await pool.fetch("SELECT email FROM review.reviewers ORDER BY email")
    return [r["email"] for r in rows]
