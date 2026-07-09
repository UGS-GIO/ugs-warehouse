# Dedicated external HTTPS load balancer for the internal review surface, IAP-gated.
#
# SAFETY: this is a BRAND-NEW, standalone LB (its own IP, cert, url-map, proxy). It does not
# reference or modify the existing public LB / url-map that serves maps-assets.geology.utah.gov.
# An apply here cannot regress public routing.

# Serverless NEG -> the review serving Cloud Run service.
resource "google_compute_region_network_endpoint_group" "review_neg" {
  name                  = "ugs-warehouse-review-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.review_serving.name
  }
}

# Backend service with IAP enabled. No Cloud CDN (restricted content must not be edge-cached).
resource "google_compute_backend_service" "review" {
  name                  = "ugs-warehouse-review-backend"
  project               = var.project_id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.review_neg.id
  }

  iap {
    enabled              = true
    oauth2_client_id     = var.iap_oauth_client_id
    oauth2_client_secret = var.iap_oauth_client_secret
  }
}

# Only @utah.gov identities may pass IAP. This is the actual access gate for the surface.
resource "google_iap_web_backend_service_iam_member" "domain_access" {
  project             = var.project_id
  web_backend_service = google_compute_backend_service.review.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "domain:${var.iap_domain}"
}

# URL map: everything on this host -> the IAP backend. Dedicated to this LB.
resource "google_compute_url_map" "review" {
  name            = "ugs-warehouse-review-urlmap"
  project         = var.project_id
  default_service = google_compute_backend_service.review.id
}

resource "google_compute_managed_ssl_certificate" "review" {
  name    = "ugs-warehouse-review-cert"
  project = var.project_id
  managed {
    domains = [var.internal_host]
  }
}

resource "google_compute_target_https_proxy" "review" {
  name             = "ugs-warehouse-review-proxy"
  project          = var.project_id
  url_map          = google_compute_url_map.review.id
  ssl_certificates = [google_compute_managed_ssl_certificate.review.id]
}

resource "google_compute_global_address" "review" {
  name    = "ugs-warehouse-review-ip"
  project = var.project_id
}

resource "google_compute_global_forwarding_rule" "review" {
  name                  = "ugs-warehouse-review-fr"
  project               = var.project_id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.review.id
  ip_address            = google_compute_global_address.review.id
  port_range            = "443"
}

# Cloud Run must allow the LB's IAP-authenticated invocations. Grant run.invoker to the IAP
# service agent (scoped to this one service, not project-wide).
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  name     = google_cloud_run_v2_service.review_serving.name
  project  = var.project_id
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}
