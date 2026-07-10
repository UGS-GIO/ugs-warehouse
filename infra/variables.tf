variable "project_id" {
  type        = string
  description = "GCP project holding the warehouse (same project as the prod public bucket)."
}

variable "region" {
  type        = string
  description = "Region for Cloud Run + the review bucket (match the existing deploys, e.g. us-central1)."
  default     = "us-central1"
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

variable "iap_domain" {
  type        = string
  description = "Google Workspace domain authorized via IAP for the internal surface."
  default     = "utah.gov"
}

# (No iap_support_email / iap_oauth_client_* vars: IAP uses Google-managed OAuth — it auto-provisions
# the client, so there's no brand/consent-screen input tofu needs. The project's OAuth consent-screen
# support email is a one-time console setting, not a tofu var.)

variable "internal_host" {
  type        = string
  description = "Hostname for the internal review surface (managed cert), e.g. review-maps.geology.utah.gov."
}

variable "labels" {
  type        = map(string)
  description = "Resource labels."
  default = {
    app       = "ugs-warehouse"
    component = "review-serving"
    managed   = "opentofu"
  }
}
