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
