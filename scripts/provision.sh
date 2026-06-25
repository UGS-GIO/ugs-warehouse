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
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-ugs-warehouse-service}"
RUNTIME_SA="${RUNTIME_SA:-warehouse-run@ut-dnr-ugs-backend-tools.iam.gserviceaccount.com}"
TOPIC="${TOPIC:-ugs-warehouse-ingest}"
SUB="${SUB:-ugs-warehouse-ingest-push}"

echo "→ topic ${TOPIC}"
gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT}" >/dev/null 2>&1 \
  || gcloud pubsub topics create "${TOPIC}" --project="${PROJECT}"

echo "→ run.invoker: ${RUNTIME_SA} on ${SERVICE} (idempotent)"
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.invoker --quiet >/dev/null

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"
echo "→ push subscription ${SUB} → ${URL}/"
if gcloud pubsub subscriptions describe "${SUB}" --project="${PROJECT}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${SUB}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}"
else
  gcloud pubsub subscriptions create "${SUB}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/" --push-auth-service-account="${RUNTIME_SA}" \
    --ack-deadline=600 --message-retention-duration=1d
fi

echo "✓ provisioned"
