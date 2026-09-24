# Monthly offline-basemap rebuild: a manual Cloud Build trigger for cloudbuild-basemap.yaml, and a
# Cloud Scheduler job that runs it on the 1st of each month. Protomaps publishes a new build
# daily, but the roads and places a field basemap shows change slowly; monthly keeps saved copies
# current without churning them. The viewer offers the update, it never forces it (index.json's
# `built` stamp, scripts/build_basemap.py). A run is one Protomaps extract of Utah plus the quad cut: cents.
#
# The trigger can also be run by hand (Run in the console, or `gcloud builds triggers run`).
#
# The job calls the trigger as its own SA, which can run builds and nothing else. That SA, its role
# and its grants need IAM admin on build_project, which the deploy SA does not hold, so they are
# set up once by hand (someone with Project IAM Admin and Role Admin there):
#
#   BP=ut-dnr-ugs-backend-tools
#   SA=ugs-basemap-scheduler@$BP.iam.gserviceaccount.com
#   DEPLOY=warehouse-deploy@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com
#   TRIGGER_SA=534590904912-compute@developer.gserviceaccount.com     # trigger_service_account
#   gcloud services enable cloudscheduler.googleapis.com --project=$BP
#   gcloud iam service-accounts create ugs-basemap-scheduler --project=$BP \
#     --display-name="Runs the monthly basemap trigger"
#   gcloud iam roles create basemapTriggerRunner --project=$BP --file=roles/basemapTriggerRunner.yaml
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$SA \
#     --role=projects/$BP/roles/basemapTriggerRunner --condition=None
#   # A run starts a build as the trigger's SA, so the caller acts as it:
#   gcloud iam service-accounts add-iam-policy-binding $TRIGGER_SA --project=$BP \
#     --member=serviceAccount:$SA --role=roles/iam.serviceAccountUser
#   # The deploy SA creates the job, and names $SA in its oauth_token, which needs actAs on it:
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$DEPLOY \
#     --role=roles/cloudscheduler.admin --condition=None
#   gcloud iam service-accounts add-iam-policy-binding $SA --project=$BP \
#     --member=serviceAccount:$DEPLOY --role=roles/iam.serviceAccountUser
#
# Then set basemap_scheduler_service_account = $SA. Empty → only the trigger is managed, no schedule.

variable "basemap_scheduler_service_account" {
  type        = string
  default     = ""
  description = "Email of the SA Cloud Scheduler calls the basemap trigger as. Empty → no monthly schedule (the trigger can still be run by hand)."
}

variable "basemap_schedule" {
  type        = string
  default     = "0 6 1 * *"
  description = "When the basemap rebuilds (cron, America/Denver). Default: 06:00 on the 1st of each month."
}

locals {
  manage_basemap_schedule = local.manage_triggers == 1 && var.basemap_scheduler_service_account != "" ? 1 : 0
}

resource "google_cloudbuild_trigger" "basemap" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-basemap"
  description     = "Offline basemap: Protomaps daily build → Utah extract → utah.pmtiles, overview and quads on the CDN. Monthly, or by hand."
  service_account = var.trigger_service_account

  # No event: it runs when the scheduler (or a person) says so, always from main.
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
  description = "Runs the ugs-warehouse-basemap trigger: a fresh offline basemap each month."
  schedule    = var.basemap_schedule
  time_zone   = "America/Denver"

  http_target {
    http_method = "POST"
    uri         = "https://cloudbuild.googleapis.com/v1/projects/${var.build_project}/locations/${var.region}/triggers/${google_cloudbuild_trigger.basemap[0].trigger_id}:run"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = var.basemap_scheduler_service_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}
