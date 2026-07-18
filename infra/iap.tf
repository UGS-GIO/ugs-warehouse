# Native Cloud Run IAP access control (no load balancer).
#
# IAP is enabled on the service itself (serving.tf, iap_enabled = true). Two bindings make it work:
#   1. Who may pass IAP  → the review group (var.iap_group) gets roles/iap.httpsResourceAccessor.
#   2. IAP may invoke it → the IAP service agent gets roles/run.invoker (it calls the service on the
#      authenticated user's behalf).
# No LB, IP, url-map, proxy, cert, or NEG — so no custom domain, no DNS record, no cert provisioning.
# The service is reached on its built-in *.run.app URL (see the serving_url output).

# 1. Only members of the review group may pass IAP (not the whole utah.gov domain). This group IS the
#    reviewer set — add/remove reviewers in the Workspace group, not here. Matches ucrc-inventory.
resource "google_iap_web_cloud_run_service_iam_member" "group_access" {
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.review_serving.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "group:${var.iap_group}"
}

# 2. Let the IAP service agent invoke the Cloud Run service (scoped to this one service).
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  name     = google_cloud_run_v2_service.review_serving.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# ---------------------------------------------------------------------------------------------------
# review_api — same native Cloud Run IAP, but reached PROGRAMMATICALLY by the public hazards-review app.
#
# review_serving is the internal viewer: a human hits the *.run.app URL and IAP does the interactive
# Google sign-in redirect. review_api is called cross-origin by the SPA with a Google id_token (aud =
# the review OAuth client); IAP validates that token instead of redirecting. Both gate on the same
# review group, and both let the IAP service agent invoke. The two extra pieces programmatic access
# needs — allow-listing the OAuth client and letting the browser's preflight through — are the
# google_iap_settings block below.
# ---------------------------------------------------------------------------------------------------

# 1. Only members of the review group may pass IAP to review_api (same group as the viewer).
resource "google_iap_web_cloud_run_service_iam_member" "review_api_group_access" {
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.review_api.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "group:${var.iap_group}"
}

# 2. Let the IAP service agent invoke review_api on the authenticated user's behalf.
resource "google_cloud_run_v2_service_iam_member" "review_api_iap_invoker" {
  name     = google_cloud_run_v2_service.review_api.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# 3. Programmatic-access IAP settings for review_api:
#    - programmatic_clients: IAP accepts an id_token whose aud is this OAuth client (the app's GIS
#      client). Without it, a browser-minted id_token is rejected even for a group member.
#    - allow_http_options: the browser fires an UNAUTHENTICATED CORS preflight (OPTIONS) before the
#      real GET; IAP would 302/401 it otherwise. This lets OPTIONS through so the authed GET can run.
#
# These were set out-of-band via `gcloud iap settings set` on review_serving during the 2026-07-14
# spike and then SILENTLY REVERTED (a later settings write replaces the whole doc — review_serving's
# are empty again today). Codifying them as a tofu resource here is what keeps them from drifting away.
resource "google_iap_settings" "review_api" {
  name = "projects/${data.google_project.this.number}/iap_web/cloud_run-${var.region}/services/${google_cloud_run_v2_service.review_api.name}"

  access_settings {
    oauth_settings {
      programmatic_clients = [var.review_iap_programmatic_client]
    }
    cors_settings {
      allow_http_options = true
    }
  }
}
