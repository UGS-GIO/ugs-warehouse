# The "front desk": API Gateway in front of the PRIVATE review-api Cloud Run service.
#
# Lets an Entra/Firebase-authed browser reach review-api without `allUsers` and without disabling the
# invoker check (both org-blocked here). The gateway validates the Firebase ID token at the edge, then
# invokes review-api AS its own service account (jwt_audience → run.invoker) — satisfying BOTH
# `run.managed.requireInvokerIam` and `iam.allowedPolicyMemberDomains`. Nothing public, no DTS ask.

resource "google_project_service" "gateway_apis" {
  for_each           = toset(["apigateway.googleapis.com", "servicemanagement.googleapis.com", "servicecontrol.googleapis.com"])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# The gateway's identity — it invokes review-api on the authenticated user's behalf.
resource "google_service_account" "review_api_gw" {
  project      = var.project_id
  account_id   = "review-api-gw"
  display_name = "ugs-warehouse review API gateway invoker"
}

# The permitted principal that opens the private door (invoker check stays ON → org-compliant).
resource "google_cloud_run_v2_service_iam_member" "gw_invoke_review_api" {
  name     = google_cloud_run_v2_service.review_api.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.review_api_gw.email}"
}

resource "google_api_gateway_api" "review_api" {
  provider   = google-beta
  project    = var.project_id
  api_id     = "ugs-warehouse-review-api"
  depends_on = [google_project_service.gateway_apis]
}

resource "google_api_gateway_api_config" "review_api" {
  provider             = google-beta
  project              = var.project_id
  api                  = google_api_gateway_api.review_api.api_id
  api_config_id_prefix = "review-"

  openapi_documents {
    document {
      path = "review-api-openapi.yaml"
      contents = base64encode(templatefile("${path.module}/review-api-openapi.yaml.tftpl", {
        backend_url = google_cloud_run_v2_service.review_api.uri
        project_id  = var.project_id
      }))
    }
  }

  # The gateway signs the backend OIDC token (jwt_audience) as this SA — the one granted run.invoker.
  gateway_config {
    backend_config {
      google_service_account = google_service_account.review_api_gw.email
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "review_api" {
  provider   = google-beta
  project    = var.project_id
  region     = var.region
  gateway_id = "ugs-warehouse-review-api"
  api_config = google_api_gateway_api_config.review_api.id
  depends_on = [google_project_service.gateway_apis]
}

# The hazards-review app calls THIS (with the Firebase Bearer token). Set as VITE_REVIEW_API_URL.
output "review_api_gateway_url" {
  description = "Public API Gateway URL for the hazards-review app (validates the Firebase token)."
  value       = "https://${google_api_gateway_gateway.review_api.default_hostname}"
}
