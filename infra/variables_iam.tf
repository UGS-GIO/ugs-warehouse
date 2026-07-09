variable "ingest_service_account" {
  type        = string
  description = "SA email the ugs-warehouse-ingest-review Cloud Run JOB runs as (needs write on the review bucket). Deployed by cloudbuild, not here."
}
