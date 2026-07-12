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

# Serving app: connect to the mappingdb Cloud SQL (review.comments) — cross-project cloudsql.client on
# the SQL instance's project. (It authenticates further as the least-priv review_writer DB role.)
resource "google_project_iam_member" "serving_sql_client" {
  project = var.sql_project
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.serving.email}"
}

# Serving app: read the review_writer password secret.
resource "google_secret_manager_secret_iam_member" "serving_db_secret" {
  project   = var.project_id
  secret_id = var.db_password_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.serving.email}"
}

# The review ingest job (deployed via cloudbuild) writes the catalog into the review bucket.
resource "google_storage_bucket_iam_member" "ingest_write_review" {
  bucket = google_storage_bucket.review.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.ingest_service_account}"
}

# The Cloud Build SA deploys the internal viewer bundle into the review bucket (cloudbuild's
# deploy-viewer-review rsync). Bucket-scoped write, gated on the SA being provided (deploy-viewer-review
# is allowFailure until then, so an empty value just no-ops the viewer publish, not the build).
resource "google_storage_bucket_iam_member" "build_write_review" {
  count  = var.build_service_account != "" ? 1 : 0
  bucket = google_storage_bucket.review.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.build_service_account}"
}

# The Cloud Build SA also ships new images to the serving service (cloudbuild's deploy-review-serving,
# cross-project: build runs in backend-tools, the service lives here). Service-scoped run.admin lets it
# update THIS service only. If a cross-project service-level grant proves insufficient (403 on update),
# fall back to impersonating the deploy SA in the cloudbuild step instead.
resource "google_cloud_run_v2_service_iam_member" "build_deploy_serving" {
  count    = var.build_service_account != "" ? 1 : 0
  name     = google_cloud_run_v2_service.review_serving.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.admin"
  member   = "serviceAccount:${var.build_service_account}"
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

# Same actAs need for the BUILD SA: cloudbuild's deploy-review-serving updates the service (which runs
# as the serving SA), so it needs serviceAccountUser on it too. Folding in per the work-box finding so
# a fresh `tf-apply` grants everything for the automated deploy — no manual follow-up.
resource "google_service_account_iam_member" "build_can_actas_serving" {
  count              = var.build_service_account != "" ? 1 : 0
  service_account_id = google_service_account.serving.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.build_service_account}"
}
