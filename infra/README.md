# infra/ — OpenTofu for the review/internal serving substrate

Provisions the **restricted-serving** path for the warehouse (ugs-warehouse#16): a private
GCS bucket (no CDN, no public access) holding the `_review` catalog, fronted by an
IAP-gated Cloud Run service so internal `@utah.gov` staff can preview it. The public
`warehouse/*` surface is untouched and stays CDN-served.

## Two-box workflow

This box (personal) has **no GCP perms** — it only authors `.tf`. The **work box** runs
plan/apply against GCP. So:

```bash
# work box, in infra/
cp terraform.tfvars.example terraform.tfvars   # fill real project/region/etc
tofu init
tofu plan -out plan.tfplan                      # REVIEW THIS DIFF before applying
tofu apply plan.tfplan
```

Hand off by committing `.tf` + the `tofu plan` output; the work box reviews the plan diff,
not a prose runbook.

## SAFETY CONTRACT — this config cannot touch prod data

Read before every apply. These are load-bearing, not stylistic:

1. **No existing bucket is a managed `resource`.** The prod public bucket
   (`ut-dnr-ugs-maps-prod-public`) and its objects are referenced *only* via a read-only
   `data "google_storage_bucket"` source (`data.tf`). Tofu has no handle to mutate or
   delete them — a `destroy` cannot reach them because they are not in state as resources.
2. **The new review bucket is destroy-proof.** `force_destroy = false` (a non-empty bucket
   refuses to delete) **and** `lifecycle { prevent_destroy = true }` (tofu errors rather
   than delete it). To intentionally remove it you must first edit this file.
3. **Separate load balancer + URL-map.** The internal surface gets its OWN LB/host; the
   existing public LB/url-map serving `maps-assets.geology.utah.gov` is never referenced
   or edited here, so prod routing cannot regress from an apply in this dir.
4. **Read-only serving.** The serving service's SA gets `roles/storage.objectViewer` on the
   review bucket only — no write, no delete, no admin.

If a `tofu plan` ever shows `destroy` or `replace` on anything named `*-public` or an
existing bucket/LB, STOP — that means a data source got miswritten as a resource.
