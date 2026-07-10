output "review_bucket" {
  value       = google_storage_bucket.review.name
  description = "Private review bucket. Set the ingest-review job's WAREHOUSE_BUCKET to this."
}

output "serving_url" {
  value       = google_cloud_run_v2_service.review_serving.uri
  description = "IAP-gated *.run.app URL of the review serving app. Use as the ingest job's WAREHOUSE_PUBLIC_BASE_URL and the internal viewer's VITE_CATALOG_URL base (…/review/stac/catalog.json)."
}

output "serving_service_account" {
  value       = google_service_account.serving.email
  description = "Read-only SA the serving app runs as."
}
