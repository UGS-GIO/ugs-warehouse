# READ-ONLY references. Nothing here is managed — these exist so we can wire IAM/outputs
# against prod without ever giving tofu a handle to mutate or delete prod resources.
#
# DO NOT convert any of these `data` blocks into `resource` blocks. A data source is looked
# up, never created/updated/destroyed. This is the mechanism that makes the SAFETY CONTRACT
# in README.md true — the prod public bucket simply is not a resource tofu can act on.

data "google_project" "this" {
  project_id = var.project_id
}

# The existing prod public bucket — referenced for documentation/assertions only.
data "google_storage_bucket" "public" {
  name = var.public_bucket
}
