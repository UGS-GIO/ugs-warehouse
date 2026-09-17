# Deploy — Cloud Run Job via Cloud Build

Run these with GCP auth + perms, or wire them into GH Actions.
The build artifact is `cloudbuild.yaml` (build image → Artifact Registry → deploy the
Cloud Run Job). The Job runs `scripts/cloudrun_entrypoint.sh --all`, which builds the
Postgres DSN from the Cloud SQL socket + the Secret-Manager password, then ingests.

## 0. Vars (edit to match the project)

```bash
export DEPLOY_PROJECT=ut-dnr-ugs-backend-tools          # where the Job + AR live (compute project)
export REGION=us-central1
export AR_REPO=ugs-warehouse
export RUNTIME_SA=warehouse-run@${DEPLOY_PROJECT}.iam.gserviceaccount.com
export SQL_INSTANCE=ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db
export SECRET=dbt-prod-db-password
```

## 1. One-time setup

```bash
# Artifact Registry docker repo
gcloud artifacts repositories create $AR_REPO \
  --repository-format=docker --location=$REGION --project=$DEPLOY_PROJECT

# Runtime SA — the identity the Job runs as (NOT the GH/deploy SA)
gcloud iam service-accounts create warehouse-run \
  --display-name="UGS warehouse Cloud Run" --project=$DEPLOY_PROJECT

# Runtime SA roles (cross-project):
#   write artifacts to the public bucket (maps-prod)
gcloud projects add-iam-policy-binding ut-dnr-ugs-maps-prod \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectAdmin
#   reach mapping-db (mappingdb-prod)
gcloud projects add-iam-policy-binding ut-dnr-ugs-mappingdb-prod \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/cloudsql.client
#   read the DB password secret
gcloud secrets add-iam-policy-binding $SECRET \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor \
  --project=$DEPLOY_PROJECT

# Cloud Build SA — deploys the Job + acts as the runtime SA
PROJ_NUM=$(gcloud projects describe $DEPLOY_PROJECT --format='value(projectNumber)')
CB_SA=${PROJ_NUM}@cloudbuild.gserviceaccount.com        # or your dedicated build SA
gcloud projects add-iam-policy-binding $DEPLOY_PROJECT \
  --member="serviceAccount:$CB_SA" --role=roles/run.developer
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member="serviceAccount:$CB_SA" --role=roles/iam.serviceAccountUser --project=$DEPLOY_PROJECT
# (Cloud Build SA usually already has artifactregistry.writer; grant if not.)

# Admin Console SA — triggers and monitors the Cloud Run jobs
gcloud iam service-accounts create warehouse-admin-run \
  --display-name="Warehouse Admin Console Runtime" --project=$DEPLOY_PROJECT

# Admin Console SA roles:
#   trigger and monitor Cloud Run jobs
gcloud projects add-iam-policy-binding $DEPLOY_PROJECT \
  --member="serviceAccount:warehouse-admin-run@${DEPLOY_PROJECT}.iam.gserviceaccount.com" \
  --role=roles/run.developer
#   act as the runtime SA to launch the jobs
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member="serviceAccount:warehouse-admin-run@${DEPLOY_PROJECT}.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountUser --project=$DEPLOY_PROJECT
#   read live + per-pub logs
gcloud projects add-iam-policy-binding $DEPLOY_PROJECT \
  --member="serviceAccount:warehouse-admin-run@${DEPLOY_PROJECT}.iam.gserviceaccount.com" \
  --role=roles/logging.viewer
#   read COGs from the public bucket
gcloud storage buckets add-iam-policy-binding gs://ut-dnr-ugs-maps-prod-public \
  --member="serviceAccount:warehouse-admin-run@${DEPLOY_PROJECT}.iam.gserviceaccount.com" \
  --role=roles/storage.objectViewer
```

## 2. Build + deploy (every release)

```bash
gcloud builds submit --config cloudbuild.yaml --project=$DEPLOY_PROJECT \
  --substitutions=_REGION=$REGION,_AR_REPO=$AR_REPO,_RUNTIME_SA=$RUNTIME_SA,_SQL_INSTANCE=$SQL_INSTANCE,_SECRET=$SECRET
```

## 3. Run the Jobs

### Vector Ingest Job
```bash
gcloud run jobs execute ugs-warehouse-ingest --region=$REGION --project=$DEPLOY_PROJECT
# logs / status
gcloud run jobs executions list --job=ugs-warehouse-ingest --region=$REGION --project=$DEPLOY_PROJECT
```

Per-topic instead of `--all`: override args at execute time —
`... execute ugs-warehouse-ingest --args=--topic,hazards.surfacefaultrupture_current ...`

### Geolmap Harvest Job (Publications)
```bash
gcloud run jobs execute geolmap-harvest --region=$REGION --project=$DEPLOY_PROJECT
# logs / status
gcloud run jobs executions list --job=geolmap-harvest --region=$REGION --project=$DEPLOY_PROJECT
```

Per-series instead of `--all`: override args at execute time —
`... execute geolmap-harvest --args=M-283 ...`

## 4. Pub/Sub event-driven ingest (dataELT #418)

> The code + `cloudbuild.yaml` wiring is done and committed; what's left is two IAM grants
> (need GCP perms), then a build to self-provision.

**Contract:** dataELT [`publish.sh` #418](https://github.com/UGS-GIO/dataELT/pull/418)
publishes `{"schema","topic"}` to the **`ugs-warehouse-ingest`** topic on every prod
`_current` promote (when `NOTIFY_WAREHOUSE=1`). The warehouse `ugs-warehouse-service`
(`service/main.py`) is the Pub/Sub **push** target — it ingests that one topic, and
**acks+skips** any schema not in `MART_SCHEMAS` (e.g. `gwportal`, separate DB).

**What cloudbuild already does** — the `wire-pubsub` step (idempotent, `allowFailure`)
creates the topic, grabs the service URL, grants `run.invoker` to `$RUNTIME_SA`, and
creates/updates the `ugs-warehouse-ingest-push` subscription → service `/`. It no-ops
until the two grants below exist, then self-wires on the next build.

```bash
# (vars from §0; plus:)
PROJ_NUM=$(gcloud projects describe $DEPLOY_PROJECT --format='value(projectNumber)')
CB_SA=${PROJ_NUM}@cloudbuild.gserviceaccount.com          # or the dedicated build SA
PUBSUB_SA=service-${PROJ_NUM}@gcp-sa-pubsub.iam.gserviceaccount.com

# (1) build SA may create/update the topic + subscription
gcloud projects add-iam-policy-binding $DEPLOY_PROJECT \
  --member="serviceAccount:$CB_SA" --role=roles/pubsub.admin

# (2) Pub/Sub service agent may mint OIDC tokens as the push SA (for authenticated
#     push to the PRIVATE service). Run once; the agent is created on first pubsub use.
gcloud beta services identity create --service=pubsub --project=$DEPLOY_PROJECT
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member="serviceAccount:$PUBSUB_SA" --role=roles/iam.serviceAccountTokenCreator \
  --project=$DEPLOY_PROJECT
```

Then rerun the §2 build — `wire-pubsub` provisions itself. Verify + smoke-test:

```bash
gcloud pubsub subscriptions describe ugs-warehouse-ingest-push --project=$DEPLOY_PROJECT
# end-to-end: publish a fake event, watch the service log an ingest
gcloud pubsub topics publish ugs-warehouse-ingest --project=$DEPLOY_PROJECT \
  --message='{"schema":"hazards","topic":"hazards_qfaults_current"}'
gcloud run services logs read ugs-warehouse-service --region=$REGION --project=$DEPLOY_PROJECT --limit=20
```

Nothing to change on the dataELT side — #418 owns `publish.sh`; the warehouse just
needed to listen. Drop `allowFailure` from `wire-pubsub` once it's green if you want the
wiring to gate future builds.

## 5. Static viewer → Firebase Hosting

The viewer deploys from **GitHub Actions**, not Cloud Build:

| workflow | trigger | lands on |
|---|---|---|
| `.github/workflows/firebase-hosting-merge.yml` | push to `main` touching `viewer/**` | live site `data-geology-utah-gov` |
| `.github/workflows/firebase-hosting-pull-request.yml` | PR touching `viewer/**` | preview channel `pr-<n>`, public no-login URL, 7-day expiry |

Both use `FirebaseExtended/action-hosting-deploy` with a service-account secret — the same pattern
and the same secret name as `ugs-map-viewer`. The preview workflow runs only for same-repo PRs, so
a fork never sees the credential, and its `channelId` is never `live`, so it cannot overwrite the
site. The action posts the preview URL as a PR comment itself.

**Why not Cloud Build.** It has no Firebase credentials, and the only role that would give it any
is project-level `roles/firebasehosting.admin` — there is no per-site or channels-only role. The
preview build identity is deliberately scoped to the previews bucket and nothing else
(`infra/iam.tf` §previews); handing it site-wide Hosting admin would let unmerged branch code
publish over production. Cloud Build still owns images, Cloud Run, and the review viewer bundle.

**Not the CDN bucket.** The viewer uses real path routes (`/map`, `/discover`, …). A Cloud LB
backend bucket cannot rewrite an unknown path to `index.html` — `--web-main-page-suffix` only
handles directory-style requests — so every deep link would 404 on reload. Firebase Hosting's
rewrites (`firebase.json`) do it. The old `gs://${_PUBLIC_BUCKET}/warehouse/viewer` copy is
retired; the bucket still serves STAC, COGs and tiles, unchanged.

The Hosting *site* is tofu-managed (`infra/firebase.tf`). Served at
**https://data.geology.utah.gov** (or the default `https://data-geology-utah-gov.web.app`).
`index.html` is `no-cache`; hashed assets are immutable (`firebase.json`).

### One-time setup

1. Add the repo secret `FIREBASE_SERVICE_ACCOUNT_UT_DNR_UGS_MAPS_PROD` — copy the value from
   `ugs-map-viewer`, which already holds a key for the same project.
2. Optional repo variables `VITE_FEATURES_BASE` / `VITE_TILES_BASE`: the deployed OGC API Features
   and tiles URLs. Cloud Build resolves these with `gcloud run services describe`; an Action has no
   GCP credentials, so they are variables here. Left unset, the viewer hides those links rather
   than printing dead ones.

### Which surface serves what

| surface | host | auth |
|---|---|---|
| prod viewer | Firebase Hosting, live channel | public |
| prod PR preview | Firebase Hosting, channel `pr-<n>` | public, no login |
| review viewer | Cloud Run `ugs-warehouse-review-serving`, `/review/viewer/` | IAP |
| review PR preview | Cloud Run `ugs-warehouse-previews`, `/viewer/pr-<n>/` | IAP |

The two Cloud Run surfaces stay behind IAP because they read the review catalog and its private
assets. A viewer PR gets both previews: they differ in catalog, and only the review one exercises
the comment and diff surfaces. `cloudbuild-review-viewer.yaml` builds the review viewer on
`viewer/**` pushes and `cloudbuild-viewer-preview.yaml` builds its per-PR preview; in both,
`VITE_CATALOG_URL` pointing at the review catalog is what `stac.ts` derives `IS_REVIEW` from.

Any bundle mounted under a prefix must be told which one, via Vite's `--base` (it is also the
router basepath, `src/mount.ts`): `/review/viewer/` for the review app, `/viewer/pr-<n>/` for a
review preview. A Firebase channel serves at a host root, so it needs no `--base`.
`src/ugs_warehouse/serve.py` already serves the right bundle's `index.html` for an unknown path
under either subtree, so no server change.

## 5a. Pub search assets → CDN

Client-side pub search (full-text + semantic) reads two kinds of static asset, both served
same-origin as the viewer (so **no CORS** — only HTTP **range** support matters, see below):

- **Search databases** `pubs/search/pubs-fts.duckdb` + `pubs/search/pubs-vss.duckdb` —
  built and uploaded by the pipeline (the `ugs-pubs-fts` / `ugs-pubs-embed` Cloud Run jobs).
  Nothing extra to deploy; they appear when the pipeline runs.
- **Query-embedding model** `pubs/models/Xenova/bge-small-en-v1.5/…` — the bge ONNX weights the
  browser loads to embed a semantic query. Self-hosted (not HuggingFace) so the read path has no
  third-party dependency. Vendor it once:

  ```bash
  ./scripts/vendor_search_assets.sh           # download bge model + rsync → gs://…/pubs/models/
  # needs storage.objectAdmin on the public bucket (same grant as §5); immutable, ~34MB, one-time
  ```

**Range support (the one real prerequisite).** duckdb-wasm queries the `.duckdb` files by
**range-reading** them (206 Partial Content — it fetches only the index pages a query touches,
never the whole file). GCS + the maps-assets CDN honour `Range`/`Accept-Ranges` on static objects
by default, so this works out of the box — but if a CDN rule ever strips `Range` on `*.duckdb`,
the whole client-side search degrades to full-file downloads. Smoke-test after deploy:

```bash
curl -sI -H 'Range: bytes=0-99' \
  https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb | grep -i '206\|content-range'
```

The viewer's engine (duckdb-wasm) and model host are self-hosted by default; `?ftsdb=`, `?vssdb=`,
`?models=`, and `?extrepo=` override them for local spikes (see `viewer/src/data/duckdb.ts`).

## 6. Cross-boundary grants — preflight (#223)

Builds run in `ut-dnr-ugs-backend-tools`; serving resources live in `ut-dnr-ugs-maps-prod`. Every
capability that crosses that boundary (or crosses into a third project, or lets one SA act as
another) needs its own IAM grant, and most of the ones below were found by a 403 mid-deploy, not by
reading a list (`infra/iam.tf`'s own comments document three of these as "found the hard way").
This table is that list. Some rows ARE Terraform-managed (`infra/iam.tf`) and this duplicates what
`tofu plan` would also confirm, cheaper and without needing deploy-SA impersonation; other rows —
marked below — are NOT and never will be (a different project's IAM, or a grant made out-of-band
pending its own issue), which is exactly why a script that only trusted `infra/iam.tf` would miss
them.

```
just check-grants
```

Reads the table below, calls `gcloud *.getIamPolicy` for each row (read-only — no GCP write perms
needed), and prints ✓/✗ per grant. Exit 1 if anything is missing. Add a row here when a new
cross-boundary capability is discovered; the script starts checking it immediately, and stops
rotting because the script fails when the table is wrong. See `scripts/check_grants.py`.

<!-- check-grants:begin -->
| principal | role | resource_type | resource | breaks_without |
|---|---|---|---|---|
| 534590904912-compute@developer.gserviceaccount.com | roles/run.admin | run_service | ut-dnr-ugs-maps-prod/us-central1/ugs-warehouse-review-serving | build SA 403s deploying the review viewer/API image (`infra/iam.tf` build_deploy_serving) |
| 534590904912-compute@developer.gserviceaccount.com | roles/run.admin | run_service | ut-dnr-ugs-maps-prod/us-central1/ugs-warehouse-review-api | build SA 403s on `run services update ugs-warehouse-review-api` (`infra/iam.tf` build_deploy_review_api) |
| 534590904912-compute@developer.gserviceaccount.com | roles/run.admin | run_service | ut-dnr-ugs-maps-prod/us-central1/ugs-warehouse-previews | main build SA can't redeploy previews on push to main — it silently pins `:latest` at apply time and never tracks the tag again (`infra/iam.tf` build_deploy_previews, #159) |
| warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/iam.serviceAccountUser | service_account | ugs-warehouse-review-srv@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | deploy SA can't actAs the serving SA — `iam.serviceaccounts.actAs denied` mid-apply (`infra/iam.tf` deploy_can_actas_serving) |
| warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/iam.serviceAccountUser | service_account | ugs-warehouse-previews@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | same actAs gap on the FIRST apply of `previews.tf` (`infra/iam.tf` deploy_can_actas_previews, #159) |
| ugs-warehouse-review-srv@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/secretmanager.secretAccessor | secret | ut-dnr-ugs-maps-prod/review-writer-db-password | review serving/API can't read the DB password — Cloud SQL connection 5xxs |
| ugs-warehouse-review-srv@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/cloudsql.client | project | ut-dnr-ugs-mappingdb-prod | **NOT Terraform-managed** — third project (dataELT's), our deploy identity has no IAM-admin there. App deploys fine but the comments API can't reach the DB (graceful 5xx) until the DB owner runs the grant by hand (`infra/iam.tf` line ~20) |
| warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/firebasehosting.admin | project | ut-dnr-ugs-maps-prod | **NOT Terraform-managed** — granted out-of-band for #220 (2026-09-03). Missing this and both Firebase deploy workflows fail closed with no image published |
| warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | projects/ut-dnr-ugs-maps-prod/roles/warehouseIapDeploy | project | ut-dnr-ugs-maps-prod | **NOT Terraform-managed** — the app stack applies AS this SA, which cannot manage its own privileged role. Without it the deploy cannot set IAP IAM/settings on review-api. Permission list lives in `infra/roles/warehouseIapDeploy.yaml` |
| warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com | roles/cloudbuild.builds.editor | project | ut-dnr-ugs-backend-tools | **NOT Terraform-managed** — needed to manage Cloud Build triggers, which live in the build project, not `project_id`. |
<!-- check-grants:end -->

A custom role's *binding* is checked above; its *permission list* is not — a policy only records
which role is bound, never what the role contains. `infra/roles/warehouseIapDeploy.yaml` holds that
list, and re-applying it is one command:

```bash
gcloud iam roles update warehouseIapDeploy \
  --project=ut-dnr-ugs-maps-prod --file=infra/roles/warehouseIapDeploy.yaml
```

`534590904912-compute@developer.gserviceaccount.com` is the project's default Compute SA — see
`docs/CLOUD_BUILD_CI.md` for why Cloud Build triggers run as this identity rather than
`warehouse-deploy@`, despite the latter being the CI/CD identity everywhere else.

## Scheduling (optional)

Cloud Scheduler → Cloud Run Jobs for a nightly full re-ingest:
```bash
gcloud scheduler jobs create http warehouse-nightly \
  --schedule="0 6 * * *" --uri="https://${REGION}-run.googleapis.com/v2/projects/${DEPLOY_PROJECT}/locations/${REGION}/jobs/ugs-warehouse-ingest:run" \
  --http-method=POST --oauth-service-account-email=$RUNTIME_SA --location=$REGION
```

## Notes / gotchas

- **No `cloud_sql_proxy` on Cloud Run.** `--set-cloudsql-instances` mounts the socket at
  `/cloudsql/<instance>`; the entrypoint sets `host=/cloudsql/<instance>` in the DSN.
- **schema_owner is over-privileged** as the runtime read identity — fine to ship, but
  the `schema_reader` least-priv login is the prod-hardening follow-up.
- **Org default Cloud Build SA** may be disabled by policy — if so, use a dedicated build
  SA and grant it the same roles.
- **GeoParquet/PMTiles/STAC** write to GCS via obstore (ADC = the runtime SA on Cloud Run,
  no HMAC). DuckLake writes via obstore-fsspec — same auth. No gcsfuse, no GCS extension.
- **Service (Pub/Sub push)** — deployed by `cloudbuild.yaml` (`deploy-service`) and wired
  by the `wire-pubsub` step. See **§4** for the two IAM grants still needed (GCP perms).
