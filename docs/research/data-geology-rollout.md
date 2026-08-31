# Decision: host the discovery viewer on Firebase from this repo (no split)

**Status:** decided 2026-08-31. Supersedes the earlier draft that proposed a standalone repo.

**TL;DR:** Serve the warehouse viewer on **Firebase Hosting** at **`data.geology.utah.gov`**, deployed from **this repo's existing viewer build** — no separate repo. This buys the one capability Firebase actually adds (public, no-login preview URLs) plus a public custom domain, without splitting the code. Frontend/hosting only: no REST endpoint, backend conversion, STAC pipeline, or review-path change.

## Why not a separate repo

A standalone repo was considered and rejected. Review on this PR surfaced the deciding facts:

- **The review viewer is built from the same tree.** `cloudbuild.yaml:815` builds `dist-review` (the IAP review viewer) from `viewer/` (`npm run build -- --base=/review/viewer/ --outDir dist-review`), served through IAP with the review STAC + signed URLs; `/api/comments` rides that IAP session. IAP can't front Firebase Hosting, so review stays on Cloud Run. A split would therefore force either a **cross-repo review build** (ugs-warehouse building `dist-review` from the other repo at a pinned ref) or **two permanent viewer copies**.
- **The "PR off what's live" goal doesn't need a split.** The discovery redesign (PR #183) is already a PR against the live viewer in this repo.
- **The split's costs are self-inflicted.** Drift between two copies, a re-port of #183, and a freeze/retire phase all come from the split, not from Firebase.

Deploying Firebase from this repo avoids all of it, and stays reversible: if the discovery app ever becomes its own product, extract it then.

## Portability (verified — why hosting can move at all)

The viewer is a static SPA whose data URLs are absolute, so where it's hosted doesn't change what it calls:

- Catalog defaults to absolute `https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json` (`viewer/src/stac.ts`), overridable via `VITE_CATALOG_URL`.
- Features / tiles are absolute Cloud Run URLs baked at build; unset → the links hide.
- Downloads (DuckDB-WASM over CDN GeoParquet), search, PMTiles, glyphs — all absolute `maps-assets` URLs.
- **Same-origin calls are three, all of which port fine:** `/whoami` (`app.tsx:215`, `try/catch` → public mode), `/api/comments` (`comments.ts:44`, review-only — never hit in public mode), and `${BASE_URL}3d-colors/<id>.json` (`three-d-viewer.tsx:106`, a bundled asset served from the app's own origin). Bundled `viewer/public/` assets (`3d-colors/`, `favicon.svg`, `styles/`) ship with the build.

CORS confirmed for a `data.geology.utah.gov` origin — the CDN, `ugs-warehouse-features`, and `ugs-warehouse-tiles` all return `access-control-allow-origin: *`. (The viewer already runs cross-origin on `localhost` against prod.) The move touches no endpoint, so other consumers are unaffected.

## The approach

1. **`firebase.json`** in the repo — Hosting serves `viewer/dist` as an SPA (rewrite to `index.html`). The existing public viewer build already produces `viewer/dist`; Firebase serves that same output.
2. **Deploy mechanism** — *open sub-decision.* The repo moved off GitHub Actions to Cloud Build, so the leaning is a **Cloud-Build-driven** `firebase deploy` (with `firebase hosting:channel:deploy` per PR for public preview URLs) plus a thin workflow that surfaces check status publicly — rather than `FirebaseExtended/action-hosting-deploy`, which reintroduces Actions. A new hosting deploy also needs an auth path (WIF is contested — see #14).
3. **Custom domain** — add `data.geology.utah.gov` in Firebase Hosting; someone with `geology.utah.gov` DNS adds the returned record; Firebase provisions the cert. (DNS + LB live outside this repo's Terraform — an external hand-off.)
4. **The redesign** — PR #183 lands as usual; it's already a PR off the live viewer.

Unchanged throughout: REST endpoints (features / tiles / api Cloud Run), downloads, the STAC pipeline, the `maps-assets` CDN, and the review warehouse (`dist-review` on its IAP Cloud Run).

## Open items

- CI mechanism (Cloud-Build-driven vs Action) and the deploy auth path (#14).
- Firebase project (model on `ugs-map-viewer`: `…maps-prod` / `-dev`).
- Who holds DNS + GCP admin for the domain and the Firebase site.
