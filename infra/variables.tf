variable "project_id" {
  type        = string
  description = "GCP project holding the warehouse (same project as the prod public bucket)."
}

variable "region" {
  type        = string
  description = "Region for Cloud Run + the review bucket (match the existing deploys, e.g. us-central1)."
  default     = "us-central1"
}

variable "sql_instance_connection" {
  type        = string
  description = "Cloud SQL connection name (project:region:instance) of mappingdb — hosts review.comments."
  default     = "ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db"
}

variable "sql_project" {
  type        = string
  description = "Project of the Cloud SQL instance (for the serving SA's cloudsql.client grant — cross-project)."
  default     = "ut-dnr-ugs-mappingdb-prod"
}

variable "db_password_secret" {
  type        = string
  description = "Secret Manager secret (in project_id) holding the review_writer password."
  default     = "review-writer-db-password"
}

variable "build_service_account" {
  type        = string
  default     = ""
  description = "Cloud Build SA that deploys the internal viewer into the review bucket (cloudbuild deploy-viewer-review). Gets objectAdmin on the review bucket. Empty = skip (viewer publish stays allowFailure)."
}

variable "deploy_service_account" {
  type        = string
  default     = ""
  description = <<-EOT
    Optional deploy SA to impersonate at apply time (least-privilege). The human operator holds only
    roles/iam.serviceAccountTokenCreator on this SA; the SA itself carries the apply-time admin roles
    (see README "Deploy SA"). Empty = apply directly as the caller's ADC identity (so tf-check / local
    validate work with no impersonation).
  EOT
}

variable "public_bucket" {
  type        = string
  description = "EXISTING prod public bucket — referenced read-only for context; never managed here."
  default     = "ut-dnr-ugs-maps-prod-public"
}

variable "review_bucket" {
  type        = string
  description = "NEW private bucket for the _review catalog. Must not already exist as a managed resource elsewhere."
  default     = "ut-dnr-ugs-maps-prod-review"
}

variable "serving_image" {
  type        = string
  description = "Full image ref for the IAP serving app (Artifact Registry), e.g. REGION-docker.pkg.dev/PROJECT/ugs/ugs-warehouse:TAG."
}

variable "iap_group" {
  type        = string
  description = "Google Workspace group whose members may pass IAP to the review app — the reviewer set. Members are managed in the Workspace group (not GCP IAM); the app auto-provisions each into review.reviewers on first visit."
  default     = "nrugsall@utah.gov"
}

# (No iap_support_email / iap_oauth_client_* / internal_host vars: native Cloud Run IAP uses
# Google-managed OAuth + the built-in *.run.app URL — no brand, no custom domain, no cert/DNS to
# configure. The project's OAuth consent-screen support email is a one-time console setting.)

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default = {
    app       = "ugs-warehouse"
    component = "review-serving"
    managed   = "opentofu"
  }
}

variable "previews_bucket" {
  type        = string
  description = "Bucket holding per-PR preview bundles. Separate from the review bucket so the identity that writes previews needs no access to review data (#154)."
  default     = "ut-dnr-ugs-maps-prod-previews"
}

variable "preview_service_account" {
  type        = string
  description = "Cloud Build SA for the PREVIEW triggers only. Empty → no grants are created and previews are not deployable. Deliberately not the shared build SA: a preview builds unmerged branch code, so it is the least-trusted identity in the system (#75)."
  default     = ""
}

variable "alert_emails" {
  type        = list(string)
  description = <<-EOT
    Who gets the cost/volume alerts for the public bucket. A list, not a single address: the August
    2026 cost incident ran ~17 days partly because the only pipeline alerts in the org went to one
    person's inbox. Each becomes its own notification channel in project_id.
  EOT
  default     = ["clunn@utah.gov", "marshallrobinson@utah.gov"]
}

variable "bucket_ops_alert_threshold" {
  type        = number
  description = <<-EOT
    GCS operations per second on public_bucket that, sustained for 30 minutes, raises an alert.
    Normal is 0.1-0.5/s; the heaviest legitimate day observed was ~2/s; the 2026-08 incident ran at
    ~3,472/s. 60 leaves ~30x headroom over maintenance while catching a runaway the same hour.
  EOT
  default     = 60
}
