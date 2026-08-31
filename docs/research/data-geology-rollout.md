# Rollout research: `data.geology.utah.gov` (viewer → Firebase)

**Status:** draft / research only — **not for merge**. Tracking the plan; no work starts until the open decisions below are confirmed.

**TL;DR:** Lift the **live warehouse viewer** into its own repo, host it on **Firebase**, and land the discovery redesign as a PR on top — so the redesign is *truly a PR off of what's live*. This is a **hosting move, not a rewrite**: no REST endpoint, backend conversion, STAC pipeline, or review-path change. A printout of the plan artifact is alongside this file (`data-geology-rollout.pdf`).

---

## Why this is safe — the portability finding (verified)

The existing viewer is a **static SPA whose every data URL is absolute**, so where it's *hosted* doesn't change what it *calls*:

- **Catalog** — defaults to the absolute `https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json` (`viewer/src/stac.ts`), build-time overridable via `VITE_CATALOG_URL`. Not same-origin.
- **Features / Tiles** — absolute Cloud Run URLs baked at build (`VITE_FEATURES_BASE`, `VITE_TILES_BASE`); if unset, the links simply hide (no dead links).
- **Downloads, search, PMTiles, map glyphs** — all absolute `maps-assets` CDN URLs. Downloads run client-side (DuckDB-WASM over CDN GeoParquet).
- **Only same-origin call** — `fetch("/whoami")` (`viewer/src/app.tsx`), the review-mode identity probe. It already fails → public mode on any public host (which is exactly why the dev server on `localhost` runs in public mode).

**CORS confirmed** for a `https://data.geology.utah.gov` origin — every endpoint the viewer touches returns `access-control-allow-origin: *`:

| Endpoint | Result |
| --- | --- |
| CDN — STAC catalog + GeoParquet downloads + PMTiles | `access-control-allow-origin: *` |
| `ugs-warehouse-features` (OGC API) | `access-control-allow-origin: *` |
| `ugs-warehouse-tiles` (XYZ + styles) | `access-control-allow-origin: *` |

Proof by existence: the viewer has run cross-origin on `localhost:5173` against prod throughout development. Firebase is just one more origin. The port doesn't *touch* any endpoint, so **other consumers (Arc Pro, ugs-map-viewer, anything else) are unaffected**.

---

## Current infrastructure (context)

- **Serving is 100% static STAC-on-CDN.** Producers (vector / raster / pubs) publish STAC + GeoParquet + PMTiles + COG to the public bucket `ut-dnr-ugs-maps-prod-public`, fronted by the `maps-assets.geology.utah.gov` CDN. There is no server in the read path.
- **Public read-APIs (Cloud Run):** `ugs-warehouse-features` (OGC / Arc Pro), `ugs-warehouse-tiles` (XYZ + style fragments), `ugs-warehouse-api` (pg_featureserv). All read the same public data.
- **Private review warehouse (IAP):** `ugs-warehouse-review-serving` streams a private bucket and mints **60-minute signed URLs** (`review_catalog.py`) behind IAP; `ugs-warehouse-previews` hosts per-PR previews on a separate IAP origin. The security boundary is **server-side signed URLs behind IAP**, not the `IS_REVIEW` UI flag.
- **DNS + load balancer for `maps-assets` live *outside* this repo's Terraform** (`infra/data.tf` reads the bucket as a data source). Standing up a new subdomain is therefore a DNS/cert hand-off, not a code change.

A one-screen visual of all this: see the artifact links at the bottom.

---

## The rollout, in order

**1. New repo + faithful port** *(in-lane)*
Commit 1 = the live `viewer/` **verbatim** from `ugs-warehouse` main — this commit *is* "what's live" (review code included; it stays dormant on a public host, as it already is on the CDN). Bake `VITE_FEATURES_BASE` / `VITE_TILES_BASE` to the `…-features` / `…-tiles` Cloud Run URLs; the catalog default is already the prod CDN. Vite `base:"./"` (already used in prod) works at the domain root.
*Verify:* `npm run build && npm test` green; runs locally identical to the live viewer.

**2. Firebase Hosting + CI** *(setup in-lane; a human pastes the secret)*
Add a Firebase Hosting site in the chosen project; GitHub Actions modeled on ugs-map-viewer (`FirebaseExtended/action-hosting-deploy`) — **per-PR preview channels + merge-to-live deploy**. The public no-login preview URL is the one real capability Cloud Build previews couldn't offer.
*Verify:* a throwaway PR spins up a live preview URL; merge deploys to the Firebase `.web.app` URL.

**3. Custom domain** *(needs DNS access — human hand-off)*
Add `data.geology.utah.gov` in Firebase Hosting → it returns the DNS records; someone with `geology.utah.gov` DNS adds the record; Firebase auto-provisions the TLS cert.

**4. Redesign lands as PR #1** *(in-lane)*
The discovery work (currently **PR #183** against this repo) is ported onto the new repo's `main` as a clean, reviewable diff **against the live viewer** — landing, discover split-view, restyled item detail, developers — and ships through the Firebase preview → live flow.

**5. Coexist, then cut over** *(later, on your timeline)*
The embedded `/warehouse/viewer/` keeps deploying from `ugs-warehouse` — **untouched** throughout. Once the standalone is proven, **freeze** the embedded viewer (so the two don't diverge), then retire it via a small `ugs-warehouse` PR — Clinton-gated, whenever ready.

---

## Stays untouched throughout

REST endpoints (features / tiles / api Cloud Run) · downloads (DuckDB-WASM over CDN GeoParquet) · the STAC pipeline · the `maps-assets` CDN · the review warehouse (stays on its IAP Cloud Run) · the `ugs-warehouse` Cloud Build deploy.

## Dependencies & honest risks

- **DNS / GCP admin isn't mine.** Phases 2–3 need someone who can create the Firebase site and add a DNS record — that gates go-live, so line it up early.
- **Two viewers will drift.** After the port there are two copies; Phase 5 resolves it by freezing the embedded one, but until then a fix must land in whichever is canonical.
- **Review mode is public-only here.** The public app can't do IAP + signed URLs (by design — review stays on Cloud Run), so the standalone app has no review surface.

---

## Open decisions (before any work)

| Decision | Recommendation | Notes |
| --- | --- | --- |
| Repo name | `ugs-data-viewer` (under `UGS-GIO`) | alts: `ugs-data-catalog`, `geodata-viewer` |
| Domain | `data.geology.utah.gov` | public front door; `warehouse.` also possible |
| Firebase | model on `ugs-map-viewer` | projects `…maps-prod` / `-dev`, new Hosting site, same Action |
| **Access** | **who holds DNS + GCP admin** | the real gate; may require the Clinton / IT loop-in |

Once confirmed, a dedicated Jira ticket is filed (ticket-first) and we execute phase by phase.

---

## References

- Plan artifact (visual): https://claude.ai/code/artifact/23e33eab-71e4-44ec-9a47-f41ac07f09d7 — printout committed as `data-geology-rollout.pdf`
- Infrastructure map (visual): https://claude.ai/code/artifact/b380450e-8ca1-4fdb-9984-a0a743c100f1
- The discovery redesign this ports: PR #183 (this repo)
