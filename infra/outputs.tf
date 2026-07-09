output "review_bucket" {
  value       = google_storage_bucket.review.name
  description = "Private review bucket. Set the ingest-review job's WAREHOUSE_BUCKET to this (and drop the review/ prefixes once assets live here alone)."
}

output "review_lb_ip" {
  value       = google_compute_global_address.review.address
  description = "Point the internal_host DNS A record at this, then wait for the managed cert to provision."
}

output "internal_host" {
  value       = var.internal_host
  description = "IAP-gated host serving the review catalog. Set the internal viewer's VITE_CATALOG_URL to https://<host>/review/stac/catalog.json."
}

output "serving_service_account" {
  value       = google_service_account.serving.email
  description = "Read-only SA the serving app runs as."
}
