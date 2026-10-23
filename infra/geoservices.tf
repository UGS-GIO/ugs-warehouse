# ArcGIS FeatureServer (UGS-GIO/geoparquet-featureserver): public, scale-to-zero, reads the public CDN,
# no secrets. Its cache budgets size themselves from the memory limit, so memory buys warm tiles.
# Past its limits it sheds with 503 rather than queueing, so concurrency can be high.
#
# It lives in the build project, beside the Artifact Registry that holds its image. This file owns
# its settings; cloudbuild.yaml only rolls out new images (`--image`), so the image and the traffic
# split it moves (the `prod` tag) are ignored here. The deploy SA needs roles/run.admin there.
#
# Clients reach it at https://api.geology.utah.gov, through the maps-assets load balancer in
# ut-dnr-ugs-maps-prod (docs/LOAD_BALANCER.md). That load balancer's URL map sends the host to the
# backend service below, which holds the CDN settings. Ingress admits only the load balancer, so the
# run.app URL does not bypass the CDN.

import {
  to = google_cloud_run_v2_service.geoservices
  id = "projects/${var.build_project}/locations/${var.region}/services/ugs-warehouse-geoservices"
}

resource "google_cloud_run_v2_service" "geoservices" {
  name     = "ugs-warehouse-geoservices"
  project  = var.build_project
  location = var.region
  labels   = merge(var.labels, { component = "geoservices" })

  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  template {
    service_account                  = var.runtime_service_account
    timeout                          = "60s"
    max_instance_request_concurrency = 200

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.build_project}/ugs-warehouse/ugs-geoservices:latest"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
        startup_cpu_boost = true
        # Keep CPU between requests off: scale-to-zero billing.
        cpu_idle = true
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

resource "google_cloud_run_v2_service_iam_member" "geoservices_public" {
  project  = var.build_project
  location = var.region
  name     = google_cloud_run_v2_service.geoservices.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_compute_region_network_endpoint_group" "geoservices" {
  name                  = "ugs-warehouse-geoservices"
  project               = var.build_project
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.geoservices.name
  }
}

# The CDN caches what the server marks cacheable (layer metadata and query results carry
# `public, max-age=300`; an error carries `no-store`), keyed on the full query string.
resource "google_compute_backend_service" "geoservices" {
  name                  = "ugs-warehouse-geoservices"
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

  backend {
    group = google_compute_region_network_endpoint_group.geoservices.id
  }
}

output "geoservices_url" {
  value       = "https://api.geology.utah.gov"
  description = "Public URL of the ArcGIS FeatureServer, through the maps-assets load balancer."
}

output "geoservices_backend_service" {
  value       = google_compute_backend_service.geoservices.id
  description = "Backend service the maps-assets URL map routes api.geology.utah.gov to."
}
