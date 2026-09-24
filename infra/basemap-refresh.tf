# Monthly basemap rebuild: Cloud Scheduler → Pub/Sub topic → Cloud Build trigger
# (cloudbuild-basemap.yaml). Runs on Google-managed service agents, so no SA or IAM grants.
# Run by hand: `gcloud builds triggers run ugs-warehouse-basemap`.
#
# One-time, on build_project:
#   gcloud services enable pubsub.googleapis.com cloudscheduler.googleapis.com --project=$BP
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY --role=roles/pubsub.editor
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY --role=roles/cloudscheduler.admin

variable "basemap_schedule" {
  type        = string
  default     = "0 6 1 * *"
  description = "When the basemap rebuilds (cron, America/Denver). Default: 06:00 on the 1st of each month. Empty → no schedule (the trigger can still be run by hand)."
}

locals {
  manage_basemap_schedule = local.manage_triggers == 1 && var.basemap_schedule != "" ? 1 : 0
}

resource "google_pubsub_topic" "basemap" {
  count = local.manage_triggers

  project = var.build_project
  name    = "ugs-warehouse-basemap"
  # Drop an undelivered run after a day rather than replay it late.
  message_retention_duration = "86400s"
}

resource "google_cloudbuild_trigger" "basemap" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-basemap"
  description     = "Basemap: Protomaps daily build → Utah extract → utah.pmtiles, overview and quads on the CDN. Monthly via Pub/Sub, or by hand."
  service_account = var.trigger_service_account

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
