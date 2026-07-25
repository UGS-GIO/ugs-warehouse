# Review app: prod + review, comments, history, orphans

Cross-repo contract between **ugs-warehouse** (this repo — the review serving app + comments API) and
**ugs-map-viewer** (the `/hazards-review` frontend). Internal/design doc — not on the public site.

## Goal

Inside the review app, a reviewer sees **both** published (prod) layers and pre-release (review) layers
and can **comment on both**. Prod and review comments never collide; when a review layer is promoted its
review thread becomes **read-only history** under the published layer; comments on rows that were later
deleted are flagged as **orphans**. None of this is ever visible on the public map site — comments live in
the private `review.comments` DB, served only by the review app, writable only by allow-listed editors.

## 1. Federation is done in the frontend, not the backend

Prod is already public. The review app's browser loads **two** sources and merges them in the UI:

| Source | Where | Auth | Assets |
| --- | --- | --- | --- |
| **Prod (published)** | public prod catalog on the maps-assets CDN, fetched directly | none | public CDN URLs, used as-is |
| **Review (pre-release)** | `GET /api/review-catalog` (review serving app) | IAP cookie or Firebase bearer | private review-bucket objects returned as **short-lived signed URLs** |

The frontend knows each layer's **origin** by which source it came from — that is the display badge
(`published` vs `in review`). The backend does **not** crawl or merge prod; the review API returns only
review items. (The review catalog *also* carries a STAC `rel=child` link to the prod catalog for
standards clients like QGIS/STAC Browser — the frontend ignores it and loads prod itself.)

## 2. Comments carry a `generation` — the collision fix

A layer's review and published versions share the **same STAC id** (the artifact stem is deliberately
shared). Comments disambiguate with a **`generation`** field instead of mutating ids:

- `generation = "review"` — a comment on the pre-release layer.
- `generation = "published"` — a comment on the promoted prod layer.

The frontend already knows the origin, so it sends the matching `generation`:

```
POST /api/comments        { item_ids:["wells"], generation:"published", body:"…" }   // on the prod layer
POST /api/comments        { item_ids:["wells"], generation:"review",    body:"…" }   // on the review layer
```

Same id, two independent threads → **no collision**. `review.item_status` is likewise keyed on
`(item_id, generation)`, so a layer's review status and published status are independent. A **reply**
inherits its parent's generation server-side (can't be spoofed).

> **Scope limit — single review round.** There are exactly two generations: `review` and `published`.
> This assumes each layer goes through **one** review pass, then promotion. If a *promoted* layer is sent
> back for **re-review**, the new discussion and the old frozen history would both be `generation=review`
> on the same id — i.e. the collision problem returns one level up, and marking the layer `promoted` again
> would (incorrectly) re-freeze the new round too. Supporting repeated rounds needs a round-numbered
> generation (e.g. `review.1`, `review.2`, `published`) — **not built**. Don't promise multi-round until
> the generation key carries the round.

## 3. Promotion → read-only history

Promotion (`_review` → `_current`, done upstream in dbt) does **not** move comments — nothing does.
Continuity/read-only is decided by one explicit signal:

> When a layer is promoted, set its **review-generation** status to `promoted`
> (`PUT /api/item-status/{item_id}` with `{status:"promoted", generation:"review"}`).

That **freezes** the review thread:

- `GET /api/comments?item_id=wells` returns every comment across generations, each with a **`read_only`**
  boolean. Review comments on a promoted layer come back `read_only: true`.
- The frontend renders those in a **read-only "review history" panel** under the published layer — no
  reply/edit/resolve affordances. The published-generation thread is the live one.
- Defense-in-depth: the backend also **rejects writes** (create/edit/delete) targeting a frozen review
  thread with **`409`**. The frozen review comments stay in the DB verbatim — history is preserved, never
  moved, just made read-only.

The published layer starts with a **clean** thread (no continuity), which is the intended behavior; the
old review discussion remains available as history.

## 4. Orphan detection is done in the frontend

Row comments key on a **stable domain key** (`row_key` = column name, `row_key_vals` = values), not the
ephemeral feature id — so a comment survives reingest/feature-id churn. The one case the stable key can't
survive is the **row being deleted**: the comment persists in the DB but its key now resolves to nothing.

There is **no delete event** (layers are `CREATE OR REPLACE` per ingest), and the review serving role
(`review_writer`) can't read the mart tables to check keys. So orphan detection lives where the data
already is — **the frontend**:

1. Fetch the layer's row comments: `GET /api/comments?item_id=wells&target_kind=row` (and
   `generation=…` as needed).
2. The frontend already has the layer's current features loaded (PMTiles / GeoParquet via duckdb-wasm),
   so it knows the current set of `row_key` values.
3. A row comment whose `row_key_vals` are **all absent** from the current key set is an **orphan** → show
   it in a read-only **"comments on deleted rows"** panel, so it's surfaced, not silently lost.

*Future option (not built):* the vector ingest, which has full DB access, could diff new keys against
`review.comments` and persist an `orphaned` flag for report/DuckDB consumers. The live view doesn't need it.

## 5. Backend endpoint reference

All under the review serving app; reads need any authenticated user, writes need an allow-listed editor.

| Endpoint | Notes |
| --- | --- |
| `GET /api/review-catalog` | review items only, private assets as signed URLs |
| `POST /api/comments` | body takes `generation` (default `review`); reply inherits parent's; `409` if the target review thread is frozen |
| `GET /api/comments` | filters: `item_id`, `generation`, `target_kind`, `status`, `row_val`, `column`; each row carries `read_only` |
| `PATCH /api/comments/{id}` / `DELETE …` | `409` on a frozen review thread |
| `GET /api/item-status` | filters: `item_id`, `generation` |
| `PUT /api/item-status/{item_id}` | body `{status, generation}`; `status:"promoted"` on `generation:"review"` freezes the review thread |

## Summary

- Comment on prod ✅ and on in-review ✅ — separate threads via `generation`, no collision.
- Promote → published thread is fresh; review thread freezes to read-only history under the layer.
- Deleted-row comments surface as read-only orphans (frontend set-difference).
- Everything private: `review.comments`, editors only, never on the public map site.
