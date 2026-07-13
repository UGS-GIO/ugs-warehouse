# Non-IAP twin of the review serving app. Same image + Cloud SQL + review_writer as review_serving,
# but auth is the app's own Firebase/Entra bearer-token verification (comments.py `_author`) instead of
# Google IAP — this is what the ugs-map-viewer /hazards-review app (Firebase Auth, same GCP project)
# calls cross-origin. Runs in REVIEW_API_ONLY mode so it serves ONLY /api/* and never streams the
# private review bucket. Both services share review.* on mapping-db, so comments/notifications sync.

variable "review_cors_origins" {
  type        = string
  description = "Comma-separated allowed origins for the review API (the hazards-review app)."
  default     = "https://maps.geology.utah.gov,https://ut-dnr-ugs-maps-prod.web.app,https://ut-dnr-ugs-maps-prod.firebaseapp.com,https://ut-dnr-ugs-maps-dev.web.app,https://ut-dnr-ugs-maps-dev.firebaseapp.com,http://localhost:5173"
}

variable "review_cors_origin_regex" {
  type        = string
  description = "Regex for Firebase Hosting preview-channel origins (dynamic per-PR URLs)."
  default     = "https://ut-dnr-ugs-maps-(prod|dev)--[a-z0-9-]+\\.web\\.app"
}

resource "google_cloud_run_v2_service" "review_api" {
  name     = "ugs-warehouse-review-api"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.serving.email

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.sql_instance_connection]
      }
    }

    containers {
      image   = var.serving_image
      command = ["python", "-m", "ugs_warehouse.serve"]

      env {
        name  = "REVIEW_API_ONLY"
        value = "1"
      }
      env {
        name  = "WAREHOUSE_BUCKET"
        value = google_storage_bucket.review.name
      }
      env {
        name  = "CLOUDSQL_INSTANCE"
        value = var.sql_instance_connection
      }
      env {
        name  = "DB_NAME"
        value = "seamlessgeolmap"
      }
      env {
        name  = "DB_USER"
        value = "review_writer"
      }
      env {
        name  = "FIREBASE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REVIEW_CORS_ORIGINS"
        value = var.review_cors_origins
      }
      env {
        name  = "REVIEW_CORS_ORIGIN_REGEX"
        value = var.review_cors_origin_regex
      }
      env {
        name = "DB_PASS"
        value_source {
          secret_key_ref {
            secret  = var.db_password_secret
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # cloudbuild owns image rollouts (same image as review_serving); ignore here so apply won't revert.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# Public reachability: the app authenticates each request itself (Firebase token → comments.py
# _author), so allow unauthenticated *invoke* — requests without a valid token get a 401 from the app.
resource "google_cloud_run_v2_service_iam_member" "review_api_public" {
  name     = google_cloud_run_v2_service.review_api.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "review_api_url" {
  description = "Public (Firebase-token-auth) review API base URL for the hazards-review app."
  value       = google_cloud_run_v2_service.review_api.uri
}
