# Deploy — Cloud Run Job via Cloud Build

Run these on the **work box** (has GCP auth + perms), or wire them into GH Actions.
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

## 4. Pub/Sub event-driven ingest (dataELT #418) — WORK-BOX HANDOFF

> **For Gemini on the work box.** Clinton authored this on his personal box (no GCP
> perms there). The code + `cloudbuild.yaml` wiring is done and committed; what's left
> is two IAM grants that need work-box perms, then a build to self-provision.

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

## 5. Static viewer → CDN

`cloudbuild.yaml` `build-viewer` + `deploy-viewer` build `viewer/` (Vite) and rsync
`viewer/dist` → `gs://${_PUBLIC_BUCKET}/${_VIEWER_PREFIX}` (default
`warehouse/viewer`), served at **https://maps-assets.geology.utah.gov/warehouse/viewer/**.
`index.html` is set `no-cache`; hashed assets are immutable. `deploy-viewer` is
`allowFailure` until the build SA can write the bucket:

```bash
# build SA needs objectAdmin on the public bucket (one-time)
gcloud storage buckets add-iam-policy-binding gs://$_PUBLIC_BUCKET \
  --member="serviceAccount:$CB_SA" --role=roles/storage.objectAdmin
```

(`$CB_SA` from §4.) After the grant, the next build publishes the viewer. The viewer's
default catalog is the prod STAC, so no extra config — it just works once live.

**Bare-prefix serving (one-time, needs `storage.buckets.update`).** The canonical URL is
`…/warehouse/viewer/index.html`. The *bare* prefix `…/warehouse/viewer/` returns GCS
`NoSuchKey` because the bucket has no default index document. Fix it so `…/warehouse/viewer/`
(and the clean deep-links `…/warehouse/viewer/?c=…&i=…`) resolve to `index.html`:

```bash
# MainPageSuffix makes a directory request serve that dir's index.html. Bucket-global, but
# only affects directory-style requests, so it's safe for the shared maps-assets bucket.
gcloud storage buckets update gs://$_PUBLIC_BUCKET --web-main-page-suffix=index.html
# DO NOT set --web-error-page to the viewer: a bucket-wide 404 page would return the viewer
# HTML for any missing object (a missing COG/tile would 200 with HTML). Leave NotFoundPage unset.
```

Routing is query-param (`?c=&i=&view=&l=&m=`) on the single `index.html`, so MainPageSuffix
alone is sufficient — no per-route rewrite needed. Until this is set, link to the explicit
`…/warehouse/viewer/index.html`.

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
`?models=`, and `?extrepo=` override them for local spikes (see `viewer/src/duckdb.ts` / `vsearch.ts`).

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
  by the `wire-pubsub` step. See **§4** for the two IAM grants still needed on the work box.
