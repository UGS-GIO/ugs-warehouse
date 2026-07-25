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

echo "→ topic ${TOPIC}"
gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT}" >/dev/null 2>&1 \
  || gcloud pubsub topics create "${TOPIC}" --project="${PROJECT}"

echo "→ run.invoker: ${RUNTIME_SA} on ${SERVICE} (idempotent)"
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.invoker --quiet >/dev/null

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT}" --format='value(status.url)')"
echo "→ push subscription ${SUB} → ${URL}/"
if gcloud pubsub subscriptions describe "${SUB}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${SUB}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}"
else
  gcloud pubsub subscriptions create "${SUB}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d
fi

# Raster promote path — a SEPARATE topic/subscription pushing to the service's '/raster' endpoint
# (ugs-ingest #183 publishes `{item_id}` here after it stages + versions a COG edition).
echo "→ raster topic ${RASTER_TOPIC}"
gcloud pubsub topics describe "${RASTER_TOPIC}" --project="${PROJECT}" >/dev/null 2>&1 \
  || gcloud pubsub topics create "${RASTER_TOPIC}" --project="${PROJECT}"

echo "→ raster push subscription ${RASTER_SUB} → ${URL}/raster"
if gcloud pubsub subscriptions describe "${RASTER_SUB}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${RASTER_SUB}" \
    --push-endpoint="${URL}/raster" --push-auth-service-account="${RUNTIME_SA}"
else
  gcloud pubsub subscriptions create "${RASTER_SUB}" --topic="${RASTER_TOPIC}" \
    --push-endpoint="${URL}/raster" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d
fi

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

# Weekly DuckLake maintenance — Cloud Scheduler triggers the maintenance Cloud Run job so the
# append-only catalog stays bounded/fast without anyone remembering the ops-console button. The
# scheduler calls the Cloud Run Admin API :run endpoint with an OAuth token minted for RUNTIME_SA
# (which already actAs itself + holds run.developer below), so the execution runs as RUNTIME_SA.
MAINTAIN_JOB="${MAINTAIN_JOB:-ugs-warehouse-ducklake-maintain}"
SCHED_JOB="${SCHED_JOB:-ugs-warehouse-ducklake-maintain-weekly}"
MAINTAIN_SCHEDULE="${MAINTAIN_SCHEDULE:-0 3 * * 0}"      # Sundays 03:00 (weekend, off-hours)
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
