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
```

## 2. Build + deploy (every release)

```bash
gcloud builds submit --config cloudbuild.yaml --project=$DEPLOY_PROJECT \
  --substitutions=_REGION=$REGION,_AR_REPO=$AR_REPO,_RUNTIME_SA=$RUNTIME_SA,_SQL_INSTANCE=$SQL_INSTANCE,_SECRET=$SECRET
```

## 3. Run the Job

```bash
gcloud run jobs execute ugs-warehouse-ingest --region=$REGION --project=$DEPLOY_PROJECT
# logs / status
gcloud run jobs executions list --job=ugs-warehouse-ingest --region=$REGION --project=$DEPLOY_PROJECT
```

Per-topic instead of `--all`: override args at execute time —
`... execute ugs-warehouse-ingest --args=--topic,hazards.surfacefaultrupture_current ...`

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
- **Service (Pub/Sub push)** is a separate target — add a `gcloud run deploy` step + the
  dataELT `publish.sh` emit + a push subscription when event-driven ingest is wanted.
