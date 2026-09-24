# Monthly basemap rebuild: Cloud Scheduler → Pub/Sub topic → Cloud Build trigger
# (cloudbuild-basemap.yaml). Runs on Google-managed service agents, so no SA or IAM grants.
# Run by hand: `gcloud builds triggers run ugs-warehouse-basemap`.
#
# The build runs as its own SA, which can write only under basemap/ in the public bucket: it runs
# downloaded code (pmtiles CLI, Protomaps data), so it gets none of the Compute SA's reach.
#
# One-time (BP=ut-dnr-ugs-backend-tools, DEPLOY=the deploy SA, SA=ugs-basemap-build@$BP.iam.gserviceaccount.com):
#   gcloud services enable pubsub.googleapis.com cloudscheduler.googleapis.com --project=$BP
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY --role=roles/pubsub.editor --condition=None
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY --role=roles/cloudscheduler.admin --condition=None
#   gcloud iam service-accounts create ugs-basemap-build --project=$BP --display-name="Builds and publishes the basemap"
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$SA --role=roles/logging.logWriter --condition=None
#   # Public bucket stays out of Terraform (README safety contract). Bucket-level match is for list (rsync).
#   gcloud storage buckets add-iam-policy-binding gs://ut-dnr-ugs-maps-prod-public \
#     --member=serviceAccount:$SA --role=roles/storage.objectAdmin \
#     --condition='title=basemap-only,expression=resource.type == "storage.googleapis.com/Bucket" || resource.name.startsWith("projects/_/buckets/ut-dnr-ugs-maps-prod-public/objects/basemap/")'

variable "basemap_schedule" {
  type        = string
  default     = "0 6 1 * *"
  description = "When the basemap rebuilds (cron, America/Denver). Default: 06:00 on the 1st of each month. Empty → no schedule (the trigger can still be run by hand)."
}

variable "basemap_trigger_service_account" {
  type        = string
  default     = ""
  description = "SA the basemap build runs as, as projects/…/serviceAccounts/… (ugs-basemap-build@). Empty → no basemap trigger, topic or schedule."
}

locals {
  manage_basemap          = local.manage_triggers == 1 && var.basemap_trigger_service_account != "" ? 1 : 0
  manage_basemap_schedule = local.manage_basemap == 1 && var.basemap_schedule != "" ? 1 : 0
}

# Setting a trigger's SA needs actAs on it; securityReviewer lets plan read these bindings back.
resource "google_service_account_iam_member" "deploy_can_actas_basemap_sa" {
  count              = local.manage_basemap == 1 && var.deploy_service_account != "" ? 1 : 0
  service_account_id = var.basemap_trigger_service_account
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_service_account_iam_member" "deploy_can_read_basemap_sa_iam" {
  count              = local.manage_basemap == 1 && var.deploy_service_account != "" ? 1 : 0
  service_account_id = var.basemap_trigger_service_account
  role               = "roles/iam.securityReviewer"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_pubsub_topic" "basemap" {
  count = local.manage_basemap

  project = var.build_project
  name    = "ugs-warehouse-basemap"
  # Drop an undelivered run after a day rather than replay it late.
  message_retention_duration = "86400s"
}

resource "google_cloudbuild_trigger" "basemap" {
  count = local.manage_basemap

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-basemap"
  description     = "Basemap: Protomaps daily build → Utah extract → utah.pmtiles, overview and quads on the CDN. Monthly via Pub/Sub, or by hand."
  service_account = var.basemap_trigger_service_account

  pubsub_config {
    topic = google_pubsub_topic.basemap[0].id
  }

  source_to_build {
    repository = var.build_repository
    ref        = "refs/heads/main"
    repo_type  = "GITHUB"
  }

  git_file_source {
    path       = "cloudbuild-basemap.yaml"
    repository = var.build_repository
    revision   = "refs/heads/main"
    repo_type  = "GITHUB"
  }
}

resource "google_cloud_scheduler_job" "basemap" {
  count = local.manage_basemap_schedule

  project     = var.build_project
  region      = var.region
  name        = "ugs-warehouse-basemap-monthly"
  description = "Publishes to ugs-warehouse-basemap, which runs the basemap trigger: a fresh basemap each month."
  schedule    = var.basemap_schedule
  time_zone   = "America/Denver"

  pubsub_target {
    topic_name = google_pubsub_topic.basemap[0].id
    data       = base64encode("monthly")
  }
}
