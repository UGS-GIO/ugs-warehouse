# ArcGIS FeatureServer (UGS-GIO/cloud-native-geoservices): public, scale-to-zero, reads the public CDN,
# no secrets. Its cache budgets size themselves from the memory limit, so memory buys warm tiles.
# Tiles are CPU-bound, so concurrency stays near 3 requests per vCPU and more load adds instances.
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
    max_instance_request_concurrency = 12

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
        # 4 vCPU: dense tiles are CPU-bound, and in Map Viewer-like sessions 4 took 0.73x the time
        # of 2 (0.46x with the smaller archive pages). Memory past 1 GiB made no difference.
        limits = {
          cpu    = "4"
          memory = "2Gi"
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
      # Cloud Build owns env (SERVED_BUILD, which scripts/deploy_geoservice.sh compares to decide
      # when to clear the CDN cache). An env block set here would be ignored.
      template[0].containers[0].env,
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

  security_policy = google_compute_security_policy.geoservices.id

  backend {
    group = google_compute_region_network_endpoint_group.geoservices.id
  }
}

# A backend policy sees only cache misses, so it limits what reaches Cloud Run, not cached reads.
# One map pan sends tens of queries, and an office shares one IP, so the limit is high. Capacity is
# protected by Cloud Run's max_instance_count and the server shedding with 503, not by this rule.
resource "google_compute_security_policy" "geoservices" {
  name    = "ugs-warehouse-geoservices"
  project = var.build_project

  # Known shared addresses (State and UGS offices) get a looser limit, still capped against a
  # runaway script on one of them.
  dynamic "rule" {
    for_each = length(var.geoservices_trusted_ip_ranges) > 0 ? [1] : []
    content {
      action   = "throttle"
      priority = 900
      match {
        versioned_expr = "SRC_IPS_V1"
        config {
          src_ip_ranges = var.geoservices_trusted_ip_ranges
        }
      }
      rate_limit_options {
        conform_action = "allow"
        exceed_action  = "deny(429)"
        enforce_on_key = "IP"
        rate_limit_threshold {
          count        = var.geoservices_trusted_rate_limit_per_minute
          interval_sec = 60
        }
      }
    }
  }

  rule {
    action   = "throttle"
    priority = 1000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = var.geoservices_rate_limit_per_minute
        interval_sec = 60
      }
    }
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# The load balancer and the policy live in build_project, so the channels and the alert do too;
# the cost-alert channels in monitoring.tf are in project_id.
resource "google_monitoring_notification_channel" "geoservices_alert_email" {
  for_each = toset(var.alert_emails)

  project      = var.build_project
  display_name = "Geoservices alert — ${each.value}"
  type         = "email"
  labels       = { email_address = each.value }
}

# The load balancer's own request metric counts the 429s Cloud Armor returns, so this needs no
# request logging. Any sustained 429 means real clients are over the limit.
resource "google_monitoring_alert_policy" "geoservices_rate_limited" {
  project      = var.build_project
  display_name = "Geoservices — clients rate limited by Cloud Armor"
  combiner     = "OR"

  conditions {
    display_name = "HTTP 429 > ${var.geoservices_429_alert_per_second}/s for 10m on the FeatureServer or ImageServer"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"https_lb_rule\"",
        "metric.type = \"loadbalancing.googleapis.com/https/request_count\"",
        "metric.label.response_code = 429",
        "resource.label.backend_target_name = one_of(\"${google_compute_backend_service.geoservices.name}\", \"${google_compute_backend_service.imageserver.name}\")",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.geoservices_429_alert_per_second
      duration        = "600s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger { count = 1 }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      Cloud Armor is answering 429 to clients of api.geology.utah.gov over
      ${var.geoservices_rate_limit_per_minute} uncached requests a minute from one IP.

      **Find who** — the load balancer logs name the address when request logging is on:

          gcloud logging read 'resource.type="http_load_balancer" AND httpRequest.status=429' \
            --project=${var.build_project} --limit=20 \
            --format="value(httpRequest.remoteIp,httpRequest.userAgent,httpRequest.requestUrl)"

      A State or UGS office address belongs in `geoservices_trusted_ip_ranges`. A single script or
      crawler can stay limited.
    EOT
  }

  notification_channels = [for c in google_monitoring_notification_channel.geoservices_alert_email : c.id]

  user_labels = var.labels
}

output "geoservices_url" {
  value       = "https://api.geology.utah.gov"
  description = "Public URL of the ArcGIS FeatureServer, through the maps-assets load balancer."
}

output "geoservices_backend_service" {
  value       = google_compute_backend_service.geoservices.id
  description = "Backend service the maps-assets URL map routes api.geology.utah.gov to."
}
