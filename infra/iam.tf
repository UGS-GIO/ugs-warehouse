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

# Deploy SA must be able to actAs the serving SA — deploying a Cloud Run service that RUNS AS the
# serving SA requires serviceAccountUser on it (serviceAccountAdmin manages SAs but can't actAs one).
# Work-box hit this mid-apply; folding it in so a clean re-apply never needs the manual grant. Gated
# on impersonation being enabled (no deploy SA → applying as the caller, which already has actAs).
resource "google_service_account_iam_member" "deploy_can_actas_serving" {
  count              = var.deploy_service_account != "" ? 1 : 0
  service_account_id = google_service_account.serving.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}
