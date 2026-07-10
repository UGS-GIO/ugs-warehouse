# IAP serving app: a Cloud Run service that streams objects from the PRIVATE review bucket
# (with HTTP Range support, so PMTiles/COG range reads work) and serves the review STAC.
#
# Native Cloud Run IAP (no external LB): IAP is enabled ON the service and gates it directly on the
# built-in *.run.app URL — Google-managed TLS, no cert/DNS/LB to provision. `iap_enabled` is only in
# the google-beta provider today (Cloud Run IAP is GA as a product; the TF toggle is beta-gated), so
# this one resource uses google-beta. Access is granted in iap.tf (domain:utah.gov).
resource "google_cloud_run_v2_service" "review_serving" {
  provider = google-beta

  name     = "ugs-warehouse-review-serving"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  # IAP gates auth at the service; ingress can be open (unauthenticated requests are stopped by IAP).
  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = true

  template {
    service_account = google_service_account.serving.email

    containers {
      image = var.serving_image
      # Serve the private bucket read-only. Entrypoint is ugs_warehouse.serve; WAREHOUSE_BUCKET points
      # it at the review bucket, PORT is provided by Cloud Run. (No PUBLIC_BASE_URL here — serve.py only
      # streams bytes; the ingest job bakes STAC hrefs against the run.app URL, exposed as an output.)
      command = ["python", "-m", "ugs_warehouse.serve"]

      env {
        name  = "WAREHOUSE_BUCKET"
        value = google_storage_bucket.review.name
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  # Never send traffic to a half-rolled revision.
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # cloudbuild owns image rollouts (deploys :$SHORT_SHA on every push). Ignore the image here
  # so `tofu apply` doesn't revert the running revision back to var.serving_image.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}
