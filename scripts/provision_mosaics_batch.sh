#!/usr/bin/env bash
# One-time infra for the Cloud Batch mosaic bake (ALL-6048), run by a human as Owner. Idempotent.
# Nothing here bills while idle: the VM and disk are created per job by submit_mosaics_batch.sh.
#
#   PLAN=1 bash scripts/provision_mosaics_batch.sh    # print the commands only
set -euo pipefail

PROJECT="${PROJECT:-ut-dnr-ugs-backend-tools}"
REGION="${REGION:-us-west3}"
RUNTIME_SA="warehouse-run@${PROJECT}.iam.gserviceaccount.com"
NET=ugs-batch
SUBNET="ugs-batch-${REGION}"

run() { if [ -n "${PLAN:-}" ]; then echo "+ $*"; else "$@"; fi; }

# Batch is off; compute, logging and artifactregistry already are.
run gcloud services enable batch.googleapis.com --project "$PROJECT"

# The project has no default network (org policy compute.skipDefaultNetworkCreation), and Batch needs one.
# Custom-mode, one subnet, no firewall rules: the implied rules allow the egress the job needs.
gcloud compute networks describe "$NET" --project "$PROJECT" >/dev/null 2>&1 \
  || run gcloud compute networks create "$NET" --subnet-mode=custom --project "$PROJECT"
gcloud compute networks subnets describe "$SUBNET" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 \
  || run gcloud compute networks subnets create "$SUBNET" --network "$NET" --region "$REGION" \
       --range 10.20.0.0/24 --enable-private-ip-google-access --project "$PROJECT"

# The job runs as the same SA as the Cloud Run mosaics job. It already has bucket objectAdmin,
# secretAccessor on schema-reader-db-password, and cloudsql.client on the mapping-db project; the
# Batch VM additionally needs to report to Batch, write logs, and pull from Artifact Registry.
for role in roles/batch.agentReporter roles/logging.logWriter; do
  run gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${RUNTIME_SA}" --role "$role" --condition=None --quiet
done
run gcloud artifacts repositories add-iam-policy-binding ugs-warehouse --location us-central1 \
  --project "$PROJECT" --member "serviceAccount:${RUNTIME_SA}" --role roles/artifactregistry.reader
