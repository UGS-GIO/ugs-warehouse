# Firebase Hosting site for the public discovery viewer (decision: #187).
# The only public host for viewer/dist — see firebase.json at the repo root. The CDN copy under
# gs://…-public/warehouse/viewer is retired: the viewer's path routes need a rewrite to index.html
# on reload, which a Cloud LB backend bucket cannot do.
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
