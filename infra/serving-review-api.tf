# API-only twin of the review serving app (REVIEW_API_ONLY: serves /api/* only, never streams the bucket).
# It was the cross-origin door for the public app; that approach is RETIRED. The review UI is now the
# same-origin /review-stac app served BY review_serving behind IAP, so review_api is UNUSED — kept
# dormant (non-IAP, private) rather than deleted. iap_enabled=false: its programmatic-IAP config was
# reverted (Option B rides on review_serving). Both services still share review.* on mapping-db.

variable "review_cors_origins" {
  type        = string
  description = "Allowed browser origin(s) for review-data reads (review bucket CORS). Only the review-serving IAP host: the /review-stac app is served there and fetches signed GCS asset URLs (pmtiles/parquet) cross-origin. Firebase/localhost origins dropped as vestigial (no public app reads the review bucket). CORS is not the access gate (private bucket + IAP + short-lived signed URLs are)."
  default     = "https://ugs-warehouse-review-serving-ufyuidl4mq-uc.a.run.app"
}

variable "review_cors_origin_regex" {
  type        = string
  description = "Regex for Firebase Hosting preview-channel origins (dynamic per-PR URLs)."
  default     = "https://ut-dnr-ugs-maps-(prod|dev)--[a-z0-9-]+\\.web\\.app"
}

# WRITE authorization for the public hazards-review app (comments._require_editor). Reads are open to
# any authenticated user; writes need to be in one of these two allow-lists OR come in over IAP (the
# internal viewer, already trusted). Empty (the default) is FAIL-CLOSED — bearer writes 403 until one
# of these is set, so this can't accidentally open writes by omission.
variable "review_editor_emails" {
  type        = string
  description = "Comma-separated exact emails allowed to write review comments over a bearer token."
  default     = ""
}

variable "review_editor_domains" {
  type        = string
  description = "Comma-separated email domains (e.g. utah.gov) allowed to write review comments over a bearer token."
  default     = ""
}

resource "google_cloud_run_v2_service" "review_api" {
  # google-beta only so iap_enabled (beta-gated field) can be expressed to keep IAP OFF.
  provider = google-beta

  name     = "ugs-warehouse-review-api"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = false
  # RETIRED programmatic IAP: review_api is no longer the review UI's door. The review app is served
  # same-origin by review_serving behind IAP (see serving.tf / iap.tf). This service stays private and
  # unused (org blocks allUsers + requireInvokerIam), so nothing external can reach it — that's fine.

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
      # The review catalog lives under the `review/stac` prefix (the ingest job's WAREHOUSE_STAC_PREFIX),
      # NOT the default `warehouse/stac`. The /api/review-catalog crawler (serve.py review_catalog) reads
      # from here; without this it starts at the wrong prefix and returns an empty item list.
      env {
        name  = "WAREHOUSE_STAC_PREFIX"
        value = "review/stac"
      }
      # STAC item asset hrefs were written rooted at review-serving's URL (the ingest job's
      # WAREHOUSE_PUBLIC_BASE_URL). The crawler strips this base to recover each object's bucket path
      # before signing it; without a match every asset looks "external" and is left UNSIGNED (the browser
      # would get an IAP-gated URL it can't authenticate to). Must equal what ingest wrote.
      env {
        name  = "WAREHOUSE_PUBLIC_BASE_URL"
        value = google_cloud_run_v2_service.review_serving.uri
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
        name  = "REVIEW_EDITOR_EMAILS"
        value = var.review_editor_emails
      }
      env {
        name  = "REVIEW_EDITOR_DOMAINS"
        value = var.review_editor_domains
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
  # client/client_version/scaling: same drift as review_serving (#26) — a manual `gcloud run deploy`
  # (used to fix the stale-image race, see review-catalog tracker) stamped these; ignore so apply
  # doesn't fight them. scaling is the whole block (not sub-fields) for the same reason as #26: no
  # scaling{} is declared in config, so ignoring sub-fields can't stop tofu wanting to drop the block.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      scaling,
    ]
  }
}

# IAP wiring (invoker binding for the IAP service agent, httpsResourceAccessor for the review group, and
# the programmatic-client + CORS-preflight settings) lives in iap.tf, mirroring review_serving.

output "review_api_url" {
  description = "review-api base URL (private, non-IAP, currently unused — the review UI is same-origin on review-serving)."
  value       = google_cloud_run_v2_service.review_api.uri
}
