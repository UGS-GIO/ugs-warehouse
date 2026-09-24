# Monthly basemap rebuild: Cloud Scheduler publishes to a Pub/Sub topic, and the Cloud Build trigger
# for cloudbuild-basemap.yaml runs on each message. Protomaps publishes a new build daily, but the
# roads and places a field basemap shows change slowly; monthly keeps saved copies current without
# churning them. A run is one Protomaps extract of Utah plus the quad cut: cents.
#
# Pub/Sub rather than the scheduler calling the trigger's :run API: an HTTP call needs an identity
# that can create builds in the build project and act as the trigger's SA, i.e. a new SA, a custom
# role and project-level IAM that only Role Admin + Project IAM Admin could apply. A Pub/Sub target
# is published by the Cloud Scheduler service agent, and the trigger subscribes through the Cloud
# Build service agent, both within the build project, so no SA, role or grant is added anywhere.
#
# The trigger can still be run by hand: `gcloud builds triggers run ugs-warehouse-basemap`, or
# publish any message to the topic.
#
# Needs, once, on build_project (the deploy SA already manages triggers there):
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
  # A missed month is not worth replaying a week late: drop undelivered runs after a day.
  message_retention_duration = "86400s"
}

resource "google_cloudbuild_trigger" "basemap" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-basemap"
  description     = "Basemap: Protomaps daily build → Utah extract → utah.pmtiles, overview and quads on the CDN. Monthly via Pub/Sub, or by hand."
  service_account = var.trigger_service_account

  # Runs on any message to its topic (the monthly schedule, or a person), always from main.
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
