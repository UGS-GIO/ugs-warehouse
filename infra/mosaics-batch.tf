# Network for the on-demand Cloud Batch mosaic bake (infra/batch/mosaics-job.json, submitted by
# scripts/submit_mosaics_batch.sh). The job's VM and disk exist only while it runs; this subnet is the
# only standing piece, and it bills nothing while idle. Custom-mode with no firewall rules: the implied
# allow-egress rule is all the job needs. The VM keeps its external IP: Private Google Access covers
# Google APIs, but the Cloud SQL proxy dials mapping-db's public IP and there is no Cloud NAT.
#
# One-time (BP=ut-dnr-ugs-backend-tools, DEPLOY=the deploy SA, SA=warehouse-run@$BP.iam.gserviceaccount.com).
# scripts/provision_mosaics_batch.sh runs everything below except the DEPLOY grant, which only a tofu apply needs:
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY --role=roles/compute.networkAdmin --condition=None
#   gcloud services enable batch.googleapis.com --project=$BP
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$SA --role=roles/batch.agentReporter --condition=None
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$SA --role=roles/logging.logWriter --condition=None
#   gcloud artifacts repositories add-iam-policy-binding ugs-warehouse --location=us-central1 --project=$BP \
#     --member=serviceAccount:$SA --role=roles/artifactregistry.reader

variable "batch_region" {
  type        = string
  default     = "us-west3"
  description = "Region of the Batch mosaic job: the same region as the public bucket and mapping-db, so the bake's reads stay in-region."
}

resource "google_compute_network" "batch" {
  project                 = var.build_project
  name                    = "ugs-batch"
  auto_create_subnetworks = false # the org skips default networks; one explicit subnet below
}

resource "google_compute_subnetwork" "batch" {
  project                  = var.build_project
  region                   = var.batch_region
  name                     = "ugs-batch-${var.batch_region}"
  network                  = google_compute_network.batch.id
  ip_cidr_range            = "10.20.0.0/24"
  private_ip_google_access = true
}

# The network was first created by provision_mosaics_batch.sh; adopt it rather than recreate it.
# Remove these two blocks after the adopting apply: on a fresh project they fail instead of creating.
import {
  to = google_compute_network.batch
  id = "projects/${var.build_project}/global/networks/ugs-batch"
}

import {
  to = google_compute_subnetwork.batch
  id = "projects/${var.build_project}/regions/${var.batch_region}/subnetworks/ugs-batch-${var.batch_region}"
}
