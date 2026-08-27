# Row Identity — the warehouse consumer view

**Status:** the consumer half of the row-identity contract. The **canonical contract is
[`dataELT/docs/row-identity-governance.md`](https://github.com/UGS-GIO/dataELT/blob/develop/docs/row-identity-governance.md)** —
it owns the model, the frozen recipe, and the producer invariants. This file does **not** copy
them; it records what the **warehouse** must guarantee when it *consumes* the durable key, and
where each guarantee is enforced in this repo. When the two disagree, the canonical contract is
the reference and the drift here is a bug. **Jira:** epic ALL-5674; Phase 0 ALL-5738.

> **TL;DR.** A managed row's durable identity is `ugs_key`, a stable integer minted upstream in
> dbt (see the canonical contract for the recipe). The warehouse **merges DuckLake on `ugs_key`**
> and advertises it as the durable key; `feature_id` is an **ephemeral Hilbert render/transport
> label** that reshuffles every load and is never durable identity. Neither ever enters the
> change-detection hash.

---

## The two keys (as this repo sees them)

`introspect.py` names both and answers "is this topic armed?":
- `UGS_KEY = "ugs_key"` — the durable merge key (`introspect.py:22`).
- `FEATURE_ID = "feature_id"` — the ephemeral render/transport id (`introspect.py:23`).
- `has_ugs_key(con, view)` — presence probe; every consumer degrades safely to the unarmed path
  when it returns false (`introspect.py:38-40`).

A topic is **armed** once its served view carries `ugs_key`; until then the warehouse uses the
unarmed fallbacks below. Arming is per-topic and rolls out gradually, so both paths are live.

## Consumer invariants

State legend matches the canonical contract (🟢 gate · 🔵 test · ⚙️ convention · 🔴 gap).

| # | Invariant | Enforced at | State |
|---|---|---|---|
| C1 | DuckLake MERGE keys on `ugs_key`; a NULL `ugs_key` in an armed topic **fails loud before the txn** (a NULL never matches the `ON`, so it would insert-then-delete and silently vanish while the GeoParquet/PMTiles from the same run still carry it) | `sink_ducklake.py` `_merge` NULL guard + two-statement MERGE | 🟢 gate |
| C2 | Change-detection (the merge `hashdiff`) excludes **both** `ugs_key` and `feature_id`; geometry is included. `feature_id` is a Hilbert ordinal that reshuffles on any insert/delete — including it would churn ~100% of rows every load | `sink_ducklake.py` diff-column selection | ⚙️ convention |
| C3 | STAC advertises the durable key as `ugs:primary_key` on armed topics | `sink_stac.py:147-152` (stamped only when the served view carries `ugs_key`) | ⚙️ convention |
| C4 | A previously-armed topic that arrives with **no** `ugs_key` reverts to full-rewrite **with a loud signal**, not silently | `sink_ducklake.py` `_is_dearm` → loud WARNING before the fallback rewrite (still proceeds — a deliberate de-arm is legitimate) | 🔵 test |
| C5 | The surfaced feature id (MVT / OGC / Esri) follows the per-surface policy below | today hardcoded to `feature_id`; `ugs_key` is not yet an OGC id candidate | 🔴 gap (#174) |
| C6 | Viewer id lookups are hardened for a full-range `ugs_key` id (`[1, 2^53−1]`, exact as a JS `Number` — `2^53−1` is `Number.MAX_SAFE_INTEGER`) | defensive hardening for the id-flip, not a live precision bug | 🔴 gap (#174) |

**First-armed reality (not a NULL trip):** the recipe never emits NULL (per-field NULL → an `'N'`
sentinel), and a topic's first armed ingest is a `CREATE OR REPLACE` (the new `ugs_key` column is a
schema change), **not** a MERGE — so C1 is unreachable on that path. The real first-armed risk is a
**duplicate `ugs_key` among current rows**, which the serving pre-swap guard catches upstream (see
the canonical contract's P6). "Backfill the key" is a non-op — keys mint at view time.

## Per-surface feature-id policy

`feature_id` is a **transport/render label**, regenerated every load — legitimate as the MVT tile id
and the dormant/keyless fallback id. `ugs_key` is the **durable identity** — the DuckLake merge key,
the STAC comment key (`ugs:primary_key`), and the target viewer/OGC feature id.

**Where each is set.** `feature_id` is minted `1..N` in Hilbert order, tie-broken by `hash(row)` so
it is cross-ingest-deterministic (`transform.py:74-75`). It is promoted to the MVT tile id via
tippecanoe `--use-attribute-for-id=feature_id` (`sink_pmtiles.py`), and on armed topics `ugs_key`
rides the tiles as a plain property — never the tile id, so the viewer's `ugs_key`-keyed row
comments can still read it. The durable-identity surfaces are the DuckLake merge key and the
`ugs:primary_key` STAC stamp; the MVT id is the render surface.

**Open decision (blocks the id-flip — #174 / ALL-5715):** whether `ugs_key` becomes the *surfaced*
id on viewer/OGC (and Esri). Blocked on an **unmeasured** assumption — that the deployed Esri
*feature* layer uses a 32-bit OBJECTID a 53-bit `ugs_key` overflows. The ceiling is written nowhere
in code, and the OGC feature layer has never been connected to AGOL (the tile service is confirmed,
but a VectorTileServer has no OBJECTID). **Resolve by measurement, not assumption.** tippecanoe
caveat: `--use-attribute-for-id` *moves* the attribute out of tile `properties`, so a column can't be
both the MVT id and a readable property.

## Keeping this file honest

This file is kept honest by the **warehouse row-identity doc-sync gate** (§10.2 of the canonical
contract) — `scripts/check_identity_doc_sync.py`, run in CI by `tests/test_identity_doc_sync.py`.
On every build it checks that this doc cites each identity-machinery path (the vector sinks,
`introspect.py`, and `transform.py`), that each `path:line` it cites still resolves, and that it
**references** — never copies — the canonical contract. Rename or delete an identity file, drop a
citation, or let a `path:line` fall off the end of its file and the gate goes red — it guards this
doc's structural fidelity, not the semantics of each invariant (review and the §8 nightly diff cover
those). When the id-flip (#174) wires `ugs_key` onto the
featureserv OGC/Esri surface, add that path to the gate's `IDENTITY_PATHS` in the same PR that
documents it. Cross-repo drift against the canonical contract is the §8 nightly diff's backstop,
not this gate's job. When a `C*` gap here closes, update this file in the same PR.
