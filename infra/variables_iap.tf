# OAuth client for IAP. The project already uses IAP (ugs-ingest), and a GCP project can
# hold only ONE IAP brand — so we do NOT create the brand here (that would conflict). The
# work box creates/reuses an OAuth client under the existing brand and passes it in. This
# also keeps the client secret out of git.
variable "iap_oauth_client_id" {
  type        = string
  description = "OAuth 2.0 client ID for IAP (under the project's existing IAP brand)."
}

variable "iap_oauth_client_secret" {
  type        = string
  sensitive   = true
  description = "OAuth 2.0 client secret for IAP. Provide via TF_VAR_iap_oauth_client_secret / a secret, not tfvars in git."
}
