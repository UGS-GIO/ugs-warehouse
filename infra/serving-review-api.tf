# API-only twin of the review serving app. Same image + Cloud SQL + review_writer as review_serving,
# but serves ONLY /api/* (REVIEW_API_ONLY) and never streams the private review bucket. This is what
# the public ugs-map-viewer /hazards-review app calls cross-origin.
#
# Auth: native Cloud Run IAP (iap_enabled below), reached PROGRAMMATICALLY — the browser signs in with
# Google (GIS) and sends a Google id_token whose aud is the review OAuth client, allow-listed in this
# service's IAP programmaticClients (see iap.tf). IAP validates it, invokes the service, and injects the
# X-Goog-Authenticated-User-Email header, which comments.py `_author` trusts (no bearer-verify needed).
# Both services share review.* on mapping-db, so comments/notifications sync.

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

# Self-owned OAuth 2.0 Web client used by the hazards-review app's Google Sign-In. Its id_token (aud =
# this client) is what reviewers present to IAP; the client id is allow-listed in review-api's IAP
# programmaticClients (iap.tf). The client's Authorized JavaScript origins (app / preview / localhost)
# are a Console setting on the client itself — not manageable here. Proven working in the 2026-07-14 spike.
variable "review_iap_programmatic_client" {
  type        = string
  description = "OAuth client id allow-listed for programmatic IAP access to review-api (Google id_token aud)."
  default     = "328621131372-ro6m25s394ep7il7l31gtcii51fk3vc5.apps.googleusercontent.com"
}

resource "google_cloud_run_v2_service" "review_api" {
  # google-beta only for the iap_enabled toggle (beta-gated in the provider; Cloud Run IAP is GA),
  # mirroring review_serving in serving.tf.
  provider = google-beta

  name     = "ugs-warehouse-review-api"
  project  = var.project_id
  location = var.region
  labels   = var.labels

  # IAP gates auth at the service; ingress can be open (unauthenticated requests are stopped by IAP).
  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = true
  # This org enforces `run.managed.requireInvokerIam` (can't disable the invoker check) AND
  # `iam.allowedPolicyMemberDomains` (no allUsers), so the service is private. Reviewers reach /api/*
  # via native Cloud Run IAP: a Google id_token (aud = the review OAuth client, allow-listed in IAP
  # programmaticClients — iap.tf) passes IAP, which invokes the service and injects the authed-email
  # header. The old API-Gateway bridge (apigateway API org-blocked, never deployable) has been removed;
  # invoker_iam_disabled and allUsers are also org-blocked. See DEAD ENDS in the review handoff.

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
  description = "IAP-gated review API base URL the hazards-review app calls (Google id_token via IAP)."
  value       = google_cloud_run_v2_service.review_api.uri
}
