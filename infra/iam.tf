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

# NOTE — NOT managed here: the serving SA also needs roles/cloudsql.client on the mappingdb project
# (var.sql_project = ut-dnr-ugs-mappingdb-prod) to connect to Cloud SQL. That's a THIRD project (the DB
# team's) our deploy identity has no IAM-admin on, so it can't be applied from this config — it's a
# one-time grant the DB owner runs, same boundary as the review schema/role:
#   gcloud projects add-iam-policy-binding ut-dnr-ugs-mappingdb-prod \
#     --member="serviceAccount:ugs-warehouse-review-srv@ut-dnr-ugs-maps-prod.iam.gserviceaccount.com" \
#     --role="roles/cloudsql.client"
# Until it lands, the app deploys fine but the comments API can't reach the DB (graceful 5xx).

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

# Same for the non-IAP twin (cloudbuild's deploy-review-api). Without this the build SA 403s on
# `run services update ugs-warehouse-review-api` and — until deploy-review-api was made allowFailure —
# that failed the whole deploy. Same serving SA runs it, so the actAs grant above already covers it.
resource "google_cloud_run_v2_service_iam_member" "build_deploy_review_api" {
  count    = var.build_service_account != "" ? 1 : 0
  name     = google_cloud_run_v2_service.review_api.name
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

# Same actAs need, cross-project: cloudbuild-triggers.tf sets each trigger's service_account to
# trigger_service_account / preview_trigger_service_account, and creating or updating a trigger with
# a given runtime SA requires actAs on that SA — cloudbuild.builds.editor alone isn't enough, it 403s
# with "does not have impersonation permission on the trigger service account specified". Both SAs
# live in build_project, not var.project_id, hence the separate grant here instead of on the SA
# resources above.
resource "google_service_account_iam_member" "deploy_can_actas_trigger_sa" {
  count              = var.deploy_service_account != "" && var.trigger_service_account != "" ? 1 : 0
  service_account_id = var.trigger_service_account
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_service_account_iam_member" "deploy_can_actas_preview_trigger_sa" {
  count              = var.deploy_service_account != "" && var.preview_trigger_service_account != "" ? 1 : 0
  service_account_id = var.preview_trigger_service_account
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_service_account_iam_member" "deploy_can_actas_ci_trigger_sa" {
  count              = var.deploy_service_account != "" && var.ci_trigger_service_account != "" ? 1 : 0
  service_account_id = var.ci_trigger_service_account
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}

# serviceAccountUser (above) covers actAs at create/update time but not the getIamPolicy read every
# `tofu plan`/apply does to refresh these two google_service_account_iam_member resources — without
# it, every subsequent plan 403s on IAM_PERMISSION_DENIED even though nothing changed. Read-only,
# resource-scoped to just these two SAs (not roles/iam.serviceAccountViewer project-wide).
resource "google_service_account_iam_member" "deploy_can_read_trigger_sa_iam" {
  count              = var.deploy_service_account != "" && var.trigger_service_account != "" ? 1 : 0
  service_account_id = var.trigger_service_account
  role               = "roles/iam.securityReviewer"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_service_account_iam_member" "deploy_can_read_ci_trigger_sa_iam" {
  count              = var.deploy_service_account != "" && var.ci_trigger_service_account != "" ? 1 : 0
  service_account_id = var.ci_trigger_service_account
  role               = "roles/iam.securityReviewer"
  member             = "serviceAccount:${var.deploy_service_account}"
}

resource "google_service_account_iam_member" "deploy_can_read_preview_trigger_sa_iam" {
  count              = var.deploy_service_account != "" && var.preview_trigger_service_account != "" ? 1 : 0
  service_account_id = var.preview_trigger_service_account
  role               = "roles/iam.securityReviewer"
  member             = "serviceAccount:${var.deploy_service_account}"
}

# Same actAs gap, hit on the FIRST apply of previews.tf (#159): deploying google_cloud_run_v2_service
# "previews" 403'd with `iam.serviceaccounts.actAs denied on ugs-warehouse-previews` because
# serviceAccountAdmin (above) manages the SA but doesn't let the deploy SA run AS it. Folding in the
# same fix as deploy_can_actas_serving so a clean re-apply never needs the manual grant.
resource "google_service_account_iam_member" "deploy_can_actas_previews" {
  count              = var.deploy_service_account != "" ? 1 : 0
  service_account_id = google_service_account.previews.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account}"
}

# SELF-signBlob: review-api mints V4 signed GCS URLs for the private review assets via obstore, which
# calls iamcredentials `signBlob` on its OWN identity (the serving SA). On Cloud Run the ambient identity
# IS this SA, so the signBlob target = its own email — it just needs serviceAccountTokenCreator on itself.
# No exported key, nothing in Secret Manager. (Confirmed empirically: obstore's GCS signer hits
# iamcredentials.googleapis.com/.../<sa>:signBlob, so the ADC/metadata path works given this binding.)
resource "google_service_account_iam_member" "serving_sign_blob" {
  service_account_id = google_service_account.serving.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.serving.email}"
}

# --- previews ------------------------------------------------------------------------------------
# The preview build identity. Scoped to the previews bucket and nothing else — no review bucket, no
# public bucket, no project-level role. A preview is built from an UNMERGED branch, so whatever that
# branch says runs under this identity; the blast radius is one throwaway bucket.
resource "google_storage_bucket_iam_member" "preview_write_previews" {
  count  = var.preview_service_account != "" ? 1 : 0
  bucket = google_storage_bucket.previews.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.preview_service_account}"
}

# The previews SERVICE reads them. Read-only, only this bucket, and it is a different identity from
# the review serving SA — which keeps no access to previews, and gives previews none to review data.
resource "google_storage_bucket_iam_member" "previews_read" {
  bucket = google_storage_bucket.previews.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.previews.email}"
}

# NOT granted here, on purpose: nothing gives the preview SA access to the review bucket, and no
# `roles/run.*` binding exists for it. Service previews (tagged revisions) deploy to services in the
# BUILD project, which this config does not manage — those grants live with that project, and should
# be resource-scoped to the one service rather than project-wide.

# The MAIN build SA must be able to redeploy ugs-warehouse-previews on every push to main, same as
# review-serving/review-api below. Found the hard way (#159 provisioning): tofu apply pins whatever
# :latest resolves to AT APPLY TIME into an immutable revision — it does not track the tag
# afterward. Without this, previews silently runs stale code (missing whatever the next serve.py
# change was) until someone notices and redeploys it by hand, which is what happened here: the
# revision that answered #161's first real preview predated the routing logic PR #156 added, by
# the ~40 minutes it took the deploy pipeline to catch up on that same merge.
resource "google_cloud_run_v2_service_iam_member" "build_deploy_previews" {
  count    = var.build_service_account != "" ? 1 : 0
  name     = google_cloud_run_v2_service.previews.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.admin"
  member   = "serviceAccount:${var.build_service_account}"
}

resource "google_service_account_iam_member" "build_can_actas_previews" {
  count              = var.build_service_account != "" ? 1 : 0
  service_account_id = google_service_account.previews.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.build_service_account}"
}
