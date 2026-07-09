# IAP serving app: a Cloud Run service that streams objects from the PRIVATE review bucket
# (with HTTP Range support, so PMTiles/COG range reads work) and serves the review STAC.
# It is reachable only through the IAP-gated LB in iap.tf — ingress is restricted to the LB.
resource "google_cloud_run_v2_service" "review_serving" {
  name     = "ugs-warehouse-review-serving"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  # Only the internal HTTPS LB (below) may reach this service; no direct run.app URL access.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.serving.email

    containers {
      image = var.serving_image
      # Serve the private bucket read-only. The entrypoint is ugs_warehouse.serve (added in
      # src/); WAREHOUSE_BUCKET points it at the review bucket, PORT is provided by Cloud Run.
      command = ["python", "-m", "ugs_warehouse.serve"]

      env {
        name  = "WAREHOUSE_BUCKET"
        value = google_storage_bucket.review.name
      }
      # STAC hrefs must resolve to THIS internal host (same-origin, one IAP cookie), not the
      # public CDN. The serving app / ingest use this as PUBLIC_BASE_URL for the review catalog.
      env {
        name  = "WAREHOUSE_PUBLIC_BASE_URL"
        value = "https://${var.internal_host}"
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
