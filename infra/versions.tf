terraform {
  required_version = ">= 1.6" # OpenTofu 1.6+ / Terraform 1.6+

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    # google-beta is used ONLY for the Cloud Run service's iap_enabled toggle (beta-gated in the
    # provider; Cloud Run IAP itself is GA). Everything else stays on the GA google provider.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }

  # Remote state — the work box's real state lives here. `just tf-check` uses `-backend=false`, so
  # boxes without creds (personal) still fmt/validate fine; only `tofu init`/plan/apply touch the bucket.
  backend "gcs" {
    bucket = "ut-dnr-ugs-tf-state" # a state bucket, NOT one of the data buckets
    prefix = "ugs-warehouse/review-serving"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Least-privilege deploy: impersonate a dedicated SA that holds the apply-time admin roles, so no
  # human account carries standing prod admin. Empty var → null → no impersonation (caller ADC), so
  # `tf-check`/local validate still work. NOTE: this impersonates the PROVIDER only; the gcs backend
  # authenticates separately — either give the SA access to the state bucket, or run with
  # `export GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=<sa>` so the backend impersonates too.
  impersonate_service_account = var.deploy_service_account != "" ? var.deploy_service_account : null
}

# Same config as the google provider — only the Cloud Run service in serving.tf uses this (for
# iap_enabled). Impersonation must match so the beta resource applies as the same deploy SA.
provider "google-beta" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = var.deploy_service_account != "" ? var.deploy_service_account : null
}
