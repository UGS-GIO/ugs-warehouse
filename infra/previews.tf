# Per-PR preview hosting (#154): the SAME image as the review serving app, deployed as its OWN
# service so previews get their OWN ORIGIN.
#
# The origin split is the whole point, not cosmetics. A preview is built from an UNMERGED branch,
# so its JavaScript is untrusted code that a reviewer is invited to load. On the review app's
# origin that code would inherit the reviewer's IAP session and could POST/DELETE /api/comments as
# them, or call /api/review-catalog for signed URLs to private review assets and ship them
# anywhere. A different host means a different IAP cookie and CORS in between — the browser stops
# it, rather than us hoping nobody tries.
#
# `REVIEW_STATIC_ONLY` drops every /api router and every live app shell, so this service can serve
# nothing but per-PR bundles even if it is pointed at the wrong bucket.
resource "google_cloud_run_v2_service" "previews" {
  provider = google-beta

  name     = "ugs-warehouse-previews"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = true

  template {
    service_account = google_service_account.previews.email

    containers {
      image   = var.serving_image
      command = ["python", "-m", "ugs_warehouse.serve"]

      env {
        name  = "WAREHOUSE_BUCKET"
        value = google_storage_bucket.previews.name
      }
      env {
        name  = "REVIEW_STATIC_ONLY"
        value = "1"
      }
    }
    # Previews are read by humans clicking a link, one at a time. No Cloud SQL, no scale floor.
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
  }

  # Same split as review_serving/review_api: cloudbuild owns image rollouts (deploy-previews ships
  # :$SHORT_SHA on every push), so a commit tag here is correct and an apply must not revert it —
  # cloudbuild.yaml's deploy-previews comment has what that cost last time (#161).
  # `scaling` is the service-level block gcloud stamps, not the template.scaling declared above.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      scaling,
    ]
  }
}

# Its own identity: read-only, and only the previews bucket (the grant is in iam.tf). Sharing the
# review serving SA would have handed this service read access to review data it never needs.
resource "google_service_account" "previews" {
  project      = var.project_id
  account_id   = "ugs-warehouse-previews"
  display_name = "ugs-warehouse PR previews (IAP, static, read-only)"
}

# Same two IAP bindings as the review service: who may pass IAP, and IAP's own agent invoking it.
resource "google_iap_web_cloud_run_service_iam_member" "previews_group_access" {
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.previews.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "group:${var.iap_group}"
}

resource "google_cloud_run_v2_service_iam_member" "previews_iap_invoker" {
  name     = google_cloud_run_v2_service.previews.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}
