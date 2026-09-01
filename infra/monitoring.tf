# Cost/volume alerting for the prod public bucket.
#
# In August 2026 this project billed $2,206 — 3.97 BILLION GCS Class B operations from a Pub/Sub
# redelivery storm, run for ~17 days at ~$150/day. Nothing alerted, and nothing could have:
# maps-prod had no alert policies and no notification channels at all, and the pipeline policies in
# backend-tools cannot catch this shape anyway (the DLQ policy needs a dead-letter topic the
# subscription did not have, and the 5xx policy does not match 429, which is what a saturated
# Cloud Run service actually returns).
#
# The failure was economic, not error-shaped, so this alerts on the thing that actually moved:
# operation VOLUME. Stored bytes were never the problem — they were ~$50 of that bill.
#
# These are NEW resources tofu creates and owns, not an import of existing prod infrastructure —
# the SAFETY CONTRACT in data.tf (never make the prod bucket a tofu resource) is untouched.

resource "google_monitoring_notification_channel" "cost_alert_email" {
  for_each = toset(var.alert_emails)

  project      = var.project_id
  display_name = "Warehouse cost alert — ${each.value}"
  type         = "email"
  labels       = { email_address = each.value }
}

# Threshold sizing, from the real numbers:
#   normal day        ~10-40k ops/day   = 0.1-0.5/s
#   heaviest sane day  172k ops/day     = 2/s      (the 111k-object orphan sweep, 2026-09-01)
#   the incident       300M ops/day     = 3,472/s
# 60/s (~5.2M/day) sits ~30x above the worst legitimate day and ~58x below the incident, so it
# cannot be tripped by maintenance but catches a runaway within the hold-down window.
#
# 30-minute duration, deliberately: a bulk delete or a reingest can spike briefly, and an alert
# that cries wolf on routine maintenance gets muted — at which point it is worse than no alert,
# because it looks like coverage.
resource "google_monitoring_alert_policy" "public_bucket_operation_rate" {
  project      = var.project_id
  display_name = "Warehouse public bucket — sustained high GCS operation rate"
  combiner     = "OR"

  conditions {
    display_name = "GCS operations > ${var.bucket_ops_alert_threshold}/s for 30m on ${var.public_bucket}"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"gcs_bucket\"",
        "metric.type = \"storage.googleapis.com/api/request_count\"",
        "resource.label.bucket_name = \"${var.public_bucket}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.bucket_ops_alert_threshold
      duration        = "1800s"

      # Sum every method/response_code series into one ops/sec number — the incident was spread
      # across ReadObject/ListObjects/etc, so a per-series threshold would understate it.
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
      Sustained high GCS operation rate on `${var.public_bucket}`. Operations, not bytes, are what
      make a storage bill explode: 3.97B Class B ops cost ~$1,588 in August 2026 while the stored
      data cost ~$50.

      **Find the caller** — the ops themselves are not attributed, so work backwards from traffic:

          gcloud logging read 'resource.type="cloud_run_revision" AND httpRequest.requestMethod!=""' \
            --project=ut-dnr-ugs-backend-tools --limit=20 \
            --format="value(resource.labels.service_name,httpRequest.status,httpRequest.userAgent)"

      A `APIs-Google` user agent means Pub/Sub push. Paired with HTTP 429 it is a redelivery storm:
      the service saturates, returns 429, Pub/Sub retries, which saturates it further. Check the
      subscription has both a delivery cap and a backoff — `scripts/provision.sh` asserts this for
      the subscriptions it owns:

          gcloud pubsub subscriptions describe <sub> --project=ut-dnr-ugs-backend-tools \
            --format="yaml(ackDeadlineSeconds,deadLetterPolicy,retryPolicy)"

      Also worth checking: a DuckLake data path bloated with orphaned parquet makes every scan cost
      far more operations than it should. `ugs-warehouse-ducklake-maintain --args=--report` shows
      referenced files; compare with what is actually in the bucket.
    EOT
  }

  notification_channels = [for c in google_monitoring_notification_channel.cost_alert_email : c.id]

  user_labels = var.labels
}
