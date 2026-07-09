terraform {
  required_version = ">= 1.6" # OpenTofu 1.6+ / Terraform 1.6+

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote state recommended (work box). Left commented so `tofu init` works locally for
  # validate/plan; the work box uncomments + fills a real bucket before first apply.
  # backend "gcs" {
  #   bucket = "ut-dnr-ugs-tf-state"   # a state bucket that is NOT the data buckets
  #   prefix = "ugs-warehouse/review-serving"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
