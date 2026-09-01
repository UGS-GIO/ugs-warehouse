#!/usr/bin/env bash
# One-time (or rarely-run) infra provisioning for the warehouse ingest path: the Pub/Sub topic,
# the push subscription -> the private ingest service, and the run.invoker binding for the push SA.
#
# Pulled OUT of cloudbuild.yaml on purpose: code builds should only build + deploy, not re-assert
# infra on every push. This runs once after the first deploy (and again only if the service /
# topic / subscription names change). Fully idempotent + quiet — safe to re-run anytime.
#
# Runs as YOU (your gcloud auth), so the *build* SA no longer needs pubsub.admin — least privilege.
#
#   just provision            # or: PROJECT=… REGION=… bash scripts/provision.sh
set -euo pipefail

PROJECT="${PROJECT:-ut-dnr-ugs-backend-tools}"
# Force EVERY gcloud call to this project, regardless of the active gcloud config (the bucket lives in
# a different project, so an operator's active config is often the wrong one). Belt + the --project flags.
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ugs-warehouse-service}"
RUNTIME_SA="${RUNTIME_SA:-warehouse-run@ut-dnr-ugs-backend-tools.iam.gserviceaccount.com}"
TOPIC="${TOPIC:-ugs-warehouse-ingest}"      # dataELT publish.sh targets this name — the contract, do not rename
SUB="${SUB:-ugs-warehouse-ingest-push}"     # push subscription → the service '/' endpoint
RASTER_TOPIC="${RASTER_TOPIC:-ugs-warehouse-raster-promote}"  # ugs-ingest #183 raster promote publishes here
RASTER_SUB="${RASTER_SUB:-ugs-warehouse-raster-push}"        # push subscription → the service '/raster' endpoint
# dataELT's raster promote workflow (dataELT #498) authenticates as this SA to publish the promote message
RASTER_PUBLISHER="${RASTER_PUBLISHER:-github-actions-dbt@ut-dnr-ugs-backend-tools.iam.gserviceaccount.com}"
DLQ_TOPIC="${DLQ_TOPIC:-ugs-warehouse-ingest-dlq}"
RASTER_DLQ_TOPIC="${RASTER_DLQ_TOPIC:-ugs-warehouse-raster-dlq}"
MAX_DELIVERY="${MAX_DELIVERY:-5}"

# Pub/Sub's own service agent moves messages to the dead-letter topic and must be able to publish
# there + subscribe to the source. Without these two grants the dead-letter policy silently no-ops.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

# A dead-letter topic + a parking subscription on it, so capped messages are retained rather than
# dropped. Mirrors the raster-ingest / geoparquet / gdb-ingest DLQs already in this project.
ensure_dlq() {
  local dlq="$1"
  gcloud pubsub topics describe "${dlq}" --project="${PROJECT}" >/dev/null 2>&1 \
    || gcloud pubsub topics create "${dlq}" --project="${PROJECT}"
  gcloud pubsub subscriptions describe "${dlq}-parking" --project="${PROJECT}" >/dev/null 2>&1 \
    || gcloud pubsub subscriptions create "${dlq}-parking" --topic="${dlq}" --project="${PROJECT}"
  gcloud pubsub topics add-iam-policy-binding "${dlq}" --project="${PROJECT}" \
    --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.publisher --quiet >/dev/null
}

# Cap redelivery. 2026-08: this subscription had a 600s ack deadline and NO delivery cap, so a
# reingest running longer than the deadline was redelivered into itself forever — 60-110k ingests/day,
# each a full DuckLake MERGE scanning the whole table. That loop billed 3.97B GCS Class B operations
# in August. Raising the deadline is not the fix; the cap is.
retry_flags() {
  echo "--dead-letter-topic=$1 --max-delivery-attempts=${MAX_DELIVERY}" \
       "--min-retry-delay=60s --max-retry-delay=600s"
}

echo "→ topic ${TOPIC}"
gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT}" >/dev/null 2>&1 \
  || gcloud pubsub topics create "${TOPIC}" --project="${PROJECT}"

ensure_dlq "${DLQ_TOPIC}"
ensure_dlq "${RASTER_DLQ_TOPIC}"

echo "→ run.invoker: ${RUNTIME_SA} on ${SERVICE} (idempotent)"
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.invoker --quiet >/dev/null

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT}" --format='value(status.url)')"
echo "→ push subscription ${SUB} → ${URL}/"
# `--ack-deadline` / `--message-retention-duration` are asserted on BOTH branches on purpose. When
# they lived only on `create`, a subscription made by hand (or by an older version of this script)
# kept the 10s default forever and re-running never converged it — the script read as idempotent
# and wasn't. A 10s deadline redelivers a slow or crashing handler into itself every 10s, which is
# how one bad message becomes a retry storm (see the OOM in ugs-ingest#183, and #43).
if gcloud pubsub subscriptions describe "${SUB}" --project="${PROJECT}" >/dev/null 2>&1; then
  # shellcheck disable=SC2046 # retry_flags is a deliberate word-split flag list
  gcloud pubsub subscriptions update "${SUB}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d $(retry_flags "${DLQ_TOPIC}")
else
  # shellcheck disable=SC2046
  gcloud pubsub subscriptions create "${SUB}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d $(retry_flags "${DLQ_TOPIC}")
fi
gcloud pubsub subscriptions add-iam-policy-binding "${SUB}" --project="${PROJECT}" \
  --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.subscriber --quiet >/dev/null

# Raster promote path — a SEPARATE topic/subscription pushing to the service's '/raster' endpoint
# (ugs-ingest #183 publishes `{item_id}` here after it stages + versions a COG edition).
echo "→ raster topic ${RASTER_TOPIC}"
gcloud pubsub topics describe "${RASTER_TOPIC}" --project="${PROJECT}" >/dev/null 2>&1 \
  || gcloud pubsub topics create "${RASTER_TOPIC}" --project="${PROJECT}"

# Topic-level publisher grant, NOT project-wide — the emitter can publish here and nowhere else.
echo "→ pubsub.publisher: ${RASTER_PUBLISHER} on ${RASTER_TOPIC}"
gcloud pubsub topics add-iam-policy-binding "${RASTER_TOPIC}" --project="${PROJECT}" \
  --member="serviceAccount:${RASTER_PUBLISHER}" --role=roles/pubsub.publisher --quiet >/dev/null

echo "→ raster push subscription ${RASTER_SUB} → ${URL}/raster"
if gcloud pubsub subscriptions describe "${RASTER_SUB}" --project="${PROJECT}" >/dev/null 2>&1; then
  # shellcheck disable=SC2046
  gcloud pubsub subscriptions update "${RASTER_SUB}" \
    --push-endpoint="${URL}/raster" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d $(retry_flags "${RASTER_DLQ_TOPIC}")
else
  # shellcheck disable=SC2046
  gcloud pubsub subscriptions create "${RASTER_SUB}" --topic="${RASTER_TOPIC}" \
    --push-endpoint="${URL}/raster" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d $(retry_flags "${RASTER_DLQ_TOPIC}")
fi
gcloud pubsub subscriptions add-iam-policy-binding "${RASTER_SUB}" --project="${PROJECT}" \
  --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.subscriber --quiet >/dev/null

# The pubs "full refresh" orchestrator job (ugs-pubs-pipeline) runs as ${RUNTIME_SA} and shells
# `gcloud run jobs execute` against the thumbs + pubs-ingest jobs. Least privilege: grant the
# execute role at the JOB resource level (only those two jobs), NOT project-wide — so a compromised
# orchestrator can't touch any other Cloud Run resource. + actAs the runtime SA (itself) to launch
# executions that run as ${RUNTIME_SA}.
echo "→ pipeline orchestrator: execute rights on the sub-jobs only (least privilege)"
for SUBJOB in ugs-pubs-thumbs ugs-pubs-threed ugs-pubs-ingest ugs-pubs-fts ugs-pubs-embed ugs-geolmap-mosaics ugs-pubs-graph; do
  gcloud run jobs add-iam-policy-binding "${SUBJOB}" --region="${REGION}" --project="${PROJECT}" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.developer --quiet >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/iam.serviceAccountUser --quiet >/dev/null

# Daily DuckLake maintenance — Cloud Scheduler triggers the maintenance Cloud Run job so the
# append-only catalog stays bounded/fast without anyone remembering the ops-console button. The
# scheduler calls the Cloud Run Admin API :run endpoint with an OAuth token minted for RUNTIME_SA
# (which already actAs itself + holds run.developer below), so the execution runs as RUNTIME_SA.
#
# Daily, not weekly (2026-08-31): each run compacts under a budget and stops early, so cadence is
# what keeps the small-file backlog bounded. Weekly never caught up (111,635 files, ~$2.2k/mo in
# Class B ops).
MAINTAIN_JOB="${MAINTAIN_JOB:-ugs-warehouse-ducklake-maintain}"
SCHED_JOB="${SCHED_JOB:-ugs-warehouse-ducklake-maintain-weekly}"
MAINTAIN_SCHEDULE="${MAINTAIN_SCHEDULE:-0 3 * * *}"      # daily 03:00 (off-hours)
MAINTAIN_TZ="${MAINTAIN_TZ:-America/Denver}"
RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${MAINTAIN_JOB}:run"

echo "→ run.developer: ${RUNTIME_SA} on ${MAINTAIN_JOB} (scheduler runs it as this SA)"
gcloud run jobs add-iam-policy-binding "${MAINTAIN_JOB}" --region="${REGION}" --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.developer --quiet >/dev/null

echo "→ scheduler ${SCHED_JOB} → ${MAINTAIN_JOB} (${MAINTAIN_SCHEDULE} ${MAINTAIN_TZ})"
if gcloud scheduler jobs describe "${SCHED_JOB}" --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHED_JOB}" --location="${REGION}" --project="${PROJECT}" \
    --schedule="${MAINTAIN_SCHEDULE}" --time-zone="${MAINTAIN_TZ}" \
    --uri="${RUN_URI}" --http-method=POST --oauth-service-account-email="${RUNTIME_SA}" --quiet
else
  gcloud scheduler jobs create http "${SCHED_JOB}" --location="${REGION}" --project="${PROJECT}" \
    --schedule="${MAINTAIN_SCHEDULE}" --time-zone="${MAINTAIN_TZ}" \
    --uri="${RUN_URI}" --http-method=POST --oauth-service-account-email="${RUNTIME_SA}" --quiet
fi

echo "✓ provisioned"
