# Service account the IAP serving app runs as. Read-only on the review bucket — it streams
# objects to authenticated browsers, never writes.
resource "google_service_account" "serving" {
  project      = var.project_id
  account_id   = "ugs-warehouse-review-srv"
  display_name = "ugs-warehouse review serving (IAP, read-only)"
}

# All IAM below is BUCKET-SCOPED (google_storage_bucket_iam_member on the review bucket),
# never project-scoped — so it cannot widen access to the public bucket or anything else.

# Serving app: read the review bucket only.
resource "google_storage_bucket_iam_member" "serving_read_review" {
  bucket = google_storage_bucket.review.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.serving.email}"
}

# The review ingest job (deployed via cloudbuild) writes the catalog into the review bucket.
resource "google_storage_bucket_iam_member" "ingest_write_review" {
  bucket = google_storage_bucket.review.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.ingest_service_account}"
}
