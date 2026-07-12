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


_DDL = """
    CREATE TABLE IF NOT EXISTS review.comments (
      id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      item_ids   TEXT[]      NOT NULL,
      body       TEXT        NOT NULL,
      author     TEXT        NOT NULL,
      status     TEXT        NOT NULL DEFAULT 'open',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS comments_items_gin ON review.comments USING GIN (item_ids);
"""


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


def _author(request: Request) -> str:
    """The IAP-authenticated email — the comment's author. 401 if absent (shouldn't happen behind IAP)."""
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    email = raw.split(":", 1)[-1] if raw else ""
    if not email:
        raise HTTPException(status_code=401, detail="no IAP identity")
    return email


class NewComment(BaseModel):
    item_ids: list[str] = Field(min_length=1)  # 1..N STAC item ids the comment applies to
    body: str


class PatchComment(BaseModel):
    body: str | None = None
    status: str | None = None  # open | resolved


@router.post("")
async def create_comment(c: NewComment, request: Request) -> dict:
    if not c.body.strip():
        raise HTTPException(status_code=400, detail="body required")
    author = _author(request)
    pool = await _get_pool()
    row = await pool.fetchrow(
        "INSERT INTO review.comments (item_ids, body, author) VALUES ($1, $2, $3) RETURNING *",
        c.item_ids, c.body.strip(), author,
    )
    return dict(row)


@router.get("")
async def list_comments(request: Request, item_id: str | None = None, status: str | None = None) -> list[dict]:
    _author(request)  # require an authenticated session
    pool = await _get_pool()
    where, args = ["TRUE"], []
    if item_id:
        args.append(item_id)
        where.append(f"${len(args)} = ANY(item_ids)")
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    rows = await pool.fetch(
        f"SELECT * FROM review.comments WHERE {' AND '.join(where)} ORDER BY created_at DESC", *args
    )
    return [dict(r) for r in rows]


@router.patch("/{cid}")
async def patch_comment(cid: int, patch: PatchComment, request: Request) -> dict:
    author = _author(request)
    pool = await _get_pool()
    cur = await pool.fetchrow("SELECT author FROM review.comments WHERE id = $1", cid)
    if not cur:
        raise HTTPException(status_code=404, detail="not found")
    sets, args = [], []
    if patch.body is not None:  # editing text is author-only
        if cur["author"] != author:
            raise HTTPException(status_code=403, detail="only the author can edit the body")
        args.append(patch.body.strip())
        sets.append(f"body = ${len(args)}")
    if patch.status is not None:  # resolving is open to any reviewer
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
    author = _author(request)
    pool = await _get_pool()
    cur = await pool.fetchrow("SELECT author FROM review.comments WHERE id = $1", cid)
    if not cur:
        raise HTTPException(status_code=404, detail="not found")
    if cur["author"] != author:
        raise HTTPException(status_code=403, detail="only the author can delete")
    await pool.execute("DELETE FROM review.comments WHERE id = $1", cid)
    return {"deleted": cid}
