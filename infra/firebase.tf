# Firebase Hosting site for the public discovery viewer (docs/research/data-geology-rollout.md).
# Serves the same viewer/dist the CDN already serves — see firebase.json at the repo root.
#
# The site only exists here; the BUILD and DEPLOY stay in cloudbuild (viewer/dist -> hosting), the
# same split as Cloud Run services. Review is unaffected: dist-review stays on its IAP Cloud Run,
# because IAP cannot front Firebase Hosting.
resource "google_firebase_hosting_site" "discovery" {
  provider = google-beta
  project  = var.project_id
  site_id  = var.firebase_site_id
}

output "firebase_site_url" {
  description = "Default Firebase URL for the discovery viewer (custom domain is a later, DNS-gated step)."
  value       = google_firebase_hosting_site.discovery.default_url
}
