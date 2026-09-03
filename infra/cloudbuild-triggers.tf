# Cloud Build triggers. A trigger is a RESOURCE, so it belongs here for the same reason the buckets
# and the Hosting site do — the build *configs* stay in cloudbuild*.yaml, the same split as image
# rollouts (serving.tf).
#
# The scoping is the point. `included_files` decides whether a one-line CSS change rebuilds every
# service image, and while it lived only in a gcloud invocation there was nothing in the repo that
# said what the scope was — docs/CLOUD_BUILD_CI.md could record the question as "undecided" while
# production was in fact unscoped, and nobody without GCP access could tell. Here it is reviewable
# in a PR. It also takes the 2nd-gen footgun off a human: `gcloud builds triggers update` 400s on
# these, so the documented fix is delete + recreate; tofu just does the replace.
#
# DECLARED IS NOT APPLIED. Nothing here has been imported and no apply has run, so the live triggers
# are whatever the console holds. docs/CLOUD_BUILD_CI.md carries the inventory taken from
# `gcloud builds triggers describe` and is the authority on what is deployed; this file is a
# proposal until #222 imports it.
#
# NOT all triggers are here: pr-ci, docs and the two preview triggers stay console-managed. The
# preview triggers run as ugs-warehouse-preview-build@, NOT the Compute SA the two below use, so
# declaring them needs more than one trigger_service_account variable. preview-cleanup is not a
# trigger at all — it is a GitHub Action submitting a build over WIF.
#
# These live in the BUILD project, not var.project_id, so the deploy SA needs
# roles/cloudbuild.builds.editor there — a cross-project grant, like the Firebase one.

variable "build_project" {
  type        = string
  default     = "ut-dnr-ugs-backend-tools"
  description = "Project holding the Cloud Build triggers and the GitHub connection. NOT var.project_id — the build runs here, the serving resources live in maps-prod."
}

variable "build_repository" {
  type        = string
  default     = ""
  description = "2nd-gen connection repository resource name (projects/…/connections/…/repositories/ugs-warehouse). Created by the GitHub App install, not by tofu. Empty → no triggers are managed here."
}

variable "trigger_service_account" {
  type        = string
  default     = ""
  description = "SA the triggers run as, as projects/…/serviceAccounts/… . Empty → no triggers are managed here. A bare trigger-create 400s without one (docs/CLOUD_BUILD_CI.md)."
}

# Paths that actually need an image rebuild. Everything NOT listed here — viewer/**, docs/** — must
# not fire the full deploy. Kept as a variable so the scope is one reviewable list, not a flag
# buried in a create command.
variable "deploy_included_files" {
  type = list(string)
  default = [
    "src/**", "service/**", "api/**", "admin/**", "featureserv/**", "tiles/**",
    "scripts/**", "Dockerfile*", "pyproject.toml", "cloudbuild.yaml",
  ]
  description = "included_files for ugs-warehouse-deploy."
}

locals {
  manage_triggers = var.build_repository != "" && var.trigger_service_account != "" ? 1 : 0
}

# The full deploy: images + Cloud Run. Builds no viewer (see cloudbuild.yaml).
# ALREADY EXISTS — import before the first apply, or the create 409s:
#   tofu import google_cloudbuild_trigger.deploy \
#     projects/ut-dnr-ugs-backend-tools/locations/us-central1/triggers/ugs-warehouse-deploy
resource "google_cloudbuild_trigger" "deploy" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-deploy"
  description     = "Images + Cloud Run. Scoped: viewer-only pushes must not rebuild every image."
  service_account = var.trigger_service_account

  repository_event_config {
    repository = var.build_repository
    push {
      branch = "^main$"
    }
  }

  filename       = "cloudbuild.yaml"
  included_files = var.deploy_included_files
}

# The review viewer's fast path — Vite build + rsync to the private review bucket, no image builds.
# The PUBLIC viewer is not here: it deploys from .github/workflows/firebase-hosting-merge.yml,
# because Cloud Build has no Firebase credentials.
resource "google_cloudbuild_trigger" "review_viewer" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-review-viewer"
  description     = "Review viewer bundle → private review bucket, on viewer/** pushes."
  service_account = var.trigger_service_account

  repository_event_config {
    repository = var.build_repository
    push {
      branch = "^main$"
    }
  }

  filename       = "cloudbuild-review-viewer.yaml"
  included_files = ["viewer/**"]
}
