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

## Deploy SA (least-privilege apply)

Apply runs by **impersonating a dedicated deploy service account**, so no human account carries
standing admin on the prod project. The operator only needs `tokenCreator` on the SA; the SA holds the
apply-time roles. Set `deploy_service_account` in tfvars to turn it on (empty = apply as your own ADC).

**Bootstrap once** — run by someone with `projectIamAdmin` on `ut-dnr-ugs-maps-prod`:

```bash
PROJECT=ut-dnr-ugs-maps-prod
SA=warehouse-deploy@${PROJECT}.iam.gserviceaccount.com

gcloud iam service-accounts create warehouse-deploy --project=$PROJECT \
  --display-name="ugs-warehouse tofu deploy"

# Roles this tofu actually needs (scoped to the project) — nothing broader. No compute.admin:
# native Cloud Run IAP has no load-balancer/compute resources.
for R in roles/storage.admin roles/run.admin \
         roles/iap.admin roles/iam.serviceAccountAdmin; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${SA}" --role=$R --condition=None >/dev/null
done

# Let the operator impersonate it (narrow: just token minting, not the admin roles):
gcloud iam service-accounts add-iam-policy-binding $SA --project=$PROJECT \
  --member="user:clunn@utah.gov" --role=roles/iam.serviceAccountTokenCreator
```

State-bucket note: `impersonate_service_account` in the provider covers provider calls only; the gcs
backend authenticates separately. Either grant the SA read/write on the state bucket, or run apply with
`export GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=$SA` so the backend impersonates too.

## SAFETY CONTRACT — this config cannot touch prod data

Read before every apply. These are load-bearing, not stylistic:

1. **No existing bucket is a managed `resource`.** The prod public bucket
   (`ut-dnr-ugs-maps-prod-public`) and its objects are referenced *only* via a read-only
   `data "google_storage_bucket"` source (`data.tf`). Tofu has no handle to mutate or
   delete them — a `destroy` cannot reach them because they are not in state as resources.
2. **The new review bucket is destroy-proof.** `force_destroy = false` (a non-empty bucket
   refuses to delete) **and** `lifecycle { prevent_destroy = true }` (tofu errors rather
   than delete it). To intentionally remove it you must first edit this file.
3. **No load balancer at all.** Native Cloud Run IAP gates the service on its `*.run.app` URL —
   this config never references or edits the existing public LB/url-map serving
   `maps-assets.geology.utah.gov`, so prod routing cannot regress from an apply in this dir.
4. **Read-only serving.** The serving service's SA gets `roles/storage.objectViewer` on the
   review bucket only — no write, no delete, no admin.

If a `tofu plan` ever shows `destroy` or `replace` on anything named `*-public` or an
existing bucket/LB, STOP — that means a data source got miswritten as a resource.
