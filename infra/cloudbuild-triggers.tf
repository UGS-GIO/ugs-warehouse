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
# All five pre-existing triggers are imported and this file is applied — docs/CLOUD_BUILD_CI.md
# still carries the reference inventory. review-viewer was created here (it didn't exist before).
# preview-cleanup is deliberately absent: it is not a trigger, it is a GitHub Action submitting a
# build over WIF, because Cloud Build has no "PR closed" event.
#
# The PR CI trigger runs as its own SA, which only writes logs. One-time
# (BP=ut-dnr-ugs-backend-tools, SA=ugs-warehouse-ci@$BP.iam.gserviceaccount.com):
#   gcloud iam service-accounts create ugs-warehouse-ci --project=$BP --display-name="Runs PR CI"
#   gcloud projects add-iam-policy-binding $BP --member=serviceAccount:$SA --role=roles/logging.logWriter --condition=None
#
# These live in the BUILD project, not var.project_id, so the deploy SA needs, cross-project:
#   - roles/cloudbuild.builds.editor on build_project (like the Firebase grant)
#   - roles/iam.serviceAccountUser on trigger_service_account AND preview_trigger_service_account —
#     creating/updating a trigger with a given runtime SA requires actAs on it, builds.editor alone
#     403s with "does not have impersonation permission on the trigger service account specified"
#   - roles/iam.securityReviewer on those same two SAs — read-only, needed for every plan/apply to
#     refresh the actAs grants below, or it 403s on IAM_PERMISSION_DENIED with nothing changed
# All four are in iam.tf (deploy_can_actas_trigger_sa and neighbors).

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
  description = "SA the deploy/docs/CI triggers run as, as projects/…/serviceAccounts/… . The default Compute SA, NOT warehouse-deploy@ — infra/iam.tf grants the cross-project run.admin to whatever build_service_account names, and that is the Compute SA (docs/CLOUD_BUILD_CI.md §One-time setup). Empty → no triggers are managed here. Mandatory on create: org policy blocks the legacy Cloud Build SA, so a bare trigger-create 400s with a bare INVALID_ARGUMENT."
}

variable "preview_trigger_service_account" {
  type        = string
  default     = ""
  description = "SA the PR-preview triggers run as — the least-trusted identity, scoped to the previews bucket (infra/iam.tf §previews). A preview builds unmerged branch code, so it deliberately differs from trigger_service_account. Empty → the preview triggers are not managed here."
}

variable "ci_trigger_service_account" {
  type        = string
  default     = ""
  description = "SA the PR CI trigger runs as, as projects/…/serviceAccounts/… . Logs only. Empty → trigger_service_account."
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

# Paths that need a dev image rebuild. Everything NOT listed here — viewer/**, docs/** — must not
# fire the dev deploy. Kept as a variable so the scope is one reviewable list, not a flag buried in
# a create command.
variable "dev_included_files" {
  type = list(string)
  default = [
    "src/**", "api/**", "admin/**", "featureserv/**", "tiles/**",
    "Dockerfile.admin", "pyproject.toml", "cloudbuild-dev.yaml",
  ]
  description = "included_files for ugs-warehouse-dev."
}

locals {
  manage_triggers         = var.build_repository != "" && var.trigger_service_account != "" ? 1 : 0
  manage_preview_triggers = var.build_repository != "" && var.preview_trigger_service_account != "" ? 1 : 0
}

# The production deploy: images + Cloud Run, on a push to main. main only takes merges from develop
# (or an admin hotfix), so each push is a release. Builds no viewer (see cloudbuild.yaml).
# ALREADY EXISTS — import before the first apply, or the create 409s:
#   tofu import google_cloudbuild_trigger.deploy \
#     projects/ut-dnr-ugs-backend-tools/locations/us-central1/triggers/ugs-warehouse-deploy
resource "google_cloudbuild_trigger" "deploy" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-deploy"
  description     = "Production images + Cloud Run, on a push to main. Scoped: viewer-only pushes must not rebuild every image."
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

# The develop environment: `-dev` copies of the read-side services, on every push to develop.
resource "google_cloudbuild_trigger" "dev" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-dev"
  description     = "Develop environment: -dev Cloud Run services on push to develop. Code only, writes no data."
  service_account = var.trigger_service_account

  repository_event_config {
    repository = var.build_repository
    push {
      branch = "^develop$"
    }
  }

  filename       = "cloudbuild-dev.yaml"
  included_files = var.dev_included_files
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

# ---- the rest of the live triggers, transcribed from the docs/CLOUD_BUILD_CI.md inventory --------
# All four ALREADY EXIST. Import before the first apply or every create 409s — loop in
# docs/CLOUD_BUILD_CI.md.
#
# TRANSCRIBED BY HAND, AND NOTHING HERE VERIFIES THAT. `tofu validate` checks the schema, not
# whether these fields match the live triggers. The check is `tofu plan` after importing: these four
# must show NO CHANGES. A diff means this file is wrong, not the trigger — and applying would
# overwrite a working trigger with a bad included_files, comment_control or service account. Fix the
# file to match, then re-plan. Only `deploy` (update) and `review_viewer` (create) should differ.

# PR validation. Unscoped on purpose: it is test-only, and a backend change can break the viewer
# build (and vice versa) through shared config.
resource "google_cloudbuild_trigger" "pr_ci" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-pr-ci"
  service_account = coalesce(var.ci_trigger_service_account, var.trigger_service_account)

  repository_event_config {
    repository = var.build_repository
    pull_request {
      branch = "^.*$"
      # Default on create is COMMENTS_ENABLED, which holds EVERY PR build until a collaborator
      # comments /gcbrun — that is the `action_required` the first PR check came back with.
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
    }
  }

  filename = "cloudbuild-ci.yaml"
}

resource "google_cloudbuild_trigger" "docs" {
  count = local.manage_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-docs"
  service_account = var.trigger_service_account

  repository_event_config {
    repository = var.build_repository
    push {
      branch = "^main$"
    }
  }

  filename       = "cloudbuild-docs.yaml"
  included_files = ["docs/**", "mkdocs.yml", "docs-requirements.txt", "cloudbuild-docs.yaml"]
}

# The two PR previews run as the LEAST-TRUSTED identity: they build unmerged branch code.
resource "google_cloudbuild_trigger" "viewer_preview" {
  count = local.manage_preview_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-viewer-preview"
  description     = "Per-PR review viewer preview → previews bucket, behind IAP."
  service_account = var.preview_trigger_service_account

  repository_event_config {
    repository = var.build_repository
    pull_request {
      branch          = "^(main|develop)$"
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
    }
  }

  filename       = "cloudbuild-viewer-preview.yaml"
  included_files = ["viewer/**"]
}

resource "google_cloudbuild_trigger" "tiles_preview" {
  count = local.manage_preview_triggers

  project         = var.build_project
  location        = var.region
  name            = "ugs-warehouse-tiles-preview"
  description     = "Per-PR tagged Cloud Run revision of the tiles service."
  service_account = var.preview_trigger_service_account

  repository_event_config {
    repository = var.build_repository
    pull_request {
      branch          = "^(main|develop)$"
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
    }
  }

  filename       = "cloudbuild-service-preview.yaml"
  included_files = ["tiles/**"]
}

# Monthly basemap build; its topic, schedule and SA grants are in basemap-refresh.tf.
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
