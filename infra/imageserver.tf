# ArcGIS ImageServer over the geologic-map COGs (UGS-GIO/cloud-native-geoservices, imageserver/):
# public, scale-to-zero, reads the public CDN, no secrets. Set up like geoservices.tf: this file owns
# the settings, cloudbuild.yaml rolls out images, and the first deploy creates the service.
#
# Each request renders from the COG, and an export takes up to ~80 MB, so concurrency is low and
# Cloud Run adds instances instead.
#
# The maps-assets URL map in ut-dnr-ugs-maps-prod sends these paths on api.geology.utah.gov here,
# ahead of the FeatureServer default:
#   routeRules:
#   - priority: 1
#     matchRules:
#     - pathTemplateMatch: /ugs/rest/services/{id}/ImageServer
#     - pathTemplateMatch: /ugs/rest/services/{id}/ImageServer/{rest=**}
#     service: <imageserver_backend_service>

import {
  to = google_cloud_run_v2_service.imageserver
  id = "projects/${var.build_project}/locations/${var.region}/services/ugs-warehouse-imageserver"
}

resource "google_cloud_run_v2_service" "imageserver" {
  name     = "ugs-warehouse-imageserver"
  project  = var.build_project
  location = var.region
  labels   = merge(var.labels, { component = "imageserver" })

  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  template {
    service_account                  = var.runtime_service_account
    timeout                          = "60s"
    max_instance_request_concurrency = 8

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.build_project}/ugs-warehouse/ugs-imageserver:latest"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        startup_cpu_boost = true
        cpu_idle          = true
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].revision,
      traffic,
      client,
      client_version,
      scaling,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "imageserver_public" {
  project  = var.build_project
  location = var.region
  name     = google_cloud_run_v2_service.imageserver.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_compute_region_network_endpoint_group" "imageserver" {
  name                  = "ugs-warehouse-imageserver"
  project               = var.build_project
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.imageserver.name
  }
}

# Tiles and exports carry `public, max-age=300` and an ETag; errors are not cached.
resource "google_compute_backend_service" "imageserver" {
  name                  = "ugs-warehouse-imageserver"
  project               = var.build_project
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  enable_cdn            = true

  cdn_policy {
    cache_mode = "USE_ORIGIN_HEADERS"
    cache_key_policy {
      include_host         = true
      include_protocol     = true
      include_query_string = true
    }
  }

  # The FeatureServer's per-IP limit on cache misses fits a map of tiles too.
  security_policy = google_compute_security_policy.geoservices.id

  backend {
    group = google_compute_region_network_endpoint_group.imageserver.id
  }
}

output "imageserver_backend_service" {
  value       = google_compute_backend_service.imageserver.id
  description = "Backend service the maps-assets URL map routes api.geology.utah.gov ImageServer paths to."
}
