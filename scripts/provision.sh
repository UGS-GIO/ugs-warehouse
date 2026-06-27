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
TOPIC="${TOPIC:-ugs-warehouse-ingest}"      # dataELT publish.sh targets this name — the contract, do not rename
SUB="${SUB:-ugs-warehouse-ingest-push}"     # push subscription → the service '/' endpoint

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

# The pubs "full refresh" orchestrator job (ugs-pubs-pipeline) runs as ${RUNTIME_SA} and shells
# `gcloud run jobs execute` against the thumbs + pubs-ingest jobs. Least privilege: grant the
# execute role at the JOB resource level (only those two jobs), NOT project-wide — so a compromised
# orchestrator can't touch any other Cloud Run resource. + actAs the runtime SA (itself) to launch
# executions that run as ${RUNTIME_SA}.
echo "→ pipeline orchestrator: execute rights on the sub-jobs only (least privilege)"
for SUBJOB in ugs-pubs-thumbs ugs-pubs-ingest; do
  gcloud run jobs add-iam-policy-binding "${SUBJOB}" --region="${REGION}" --project="${PROJECT}" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.developer --quiet >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/iam.serviceAccountUser --quiet >/dev/null

echo "✓ provisioned"
