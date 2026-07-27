# CI on Cloud Build (not GitHub Actions)

GitHub Actions runners depend on GHA billing; when that lapses, every check goes red and nothing
merges. Cloud Build runs under the GCP project's own billing (same place deploys already run), so
moving CI there decouples it — the same pattern `ugs-ucrc-asset-management-v2` uses (all CI/CD on
Cloud Build, no GHA workflows).

## What runs

[`cloudbuild-ci.yaml`](https://github.com/UGS-GIO/ugs-warehouse/blob/main/cloudbuild-ci.yaml) — PR validation, test-only (no push/deploy):

- **backend** (`python:3.11`): `pip install -e ".[dev]"` → `ruff check .` → `pytest -q`
- **viewer** (`node:20`, `dir: viewer`): `npm ci` → `tsc --noEmit` → `eslint .` → `vitest run`

Both steps run in parallel (`waitFor: ["-"]`); either failing fails the build → the PR check goes red.

## One-time setup (in progress — #60)

1. **Connected the repo to Cloud Build** (2nd-gen host connection `ugs-warehouse-github`, repository
   resource `ugs-warehouse` under it). Console "Connect repository" 403'd until the operator held
   `roles/cloudbuild.connectionAdmin`; ended up creating the connection via CLI instead
   (`gcloud builds connections create github`), then completing the GitHub OAuth step in a browser
   and reusing the existing `UGS-GIO` org app installation rather than creating a new one.

2. **Created the triggers**, 2nd-gen form:

   ```bash
   REPO=projects/ut-dnr-ugs-backend-tools/locations/us-central1/connections/ugs-warehouse-github/repositories/ugs-warehouse
   SA=projects/ut-dnr-ugs-backend-tools/serviceAccounts/534590904912-compute@developer.gserviceaccount.com

   gcloud builds triggers create github \
     --name=ugs-warehouse-pr-ci --region=us-central1 --repository="$REPO" \
     --pull-request-pattern='^.*$' --build-config=cloudbuild-ci.yaml \
     --service-account="$SA" --project=ut-dnr-ugs-backend-tools
   ```

   `--service-account` is **mandatory** here, not optional — org policy blocks the legacy Cloud
   Build SA, so a bare trigger-create (no `--service-account`) 400s with an unhelpful
   `INVALID_ARGUMENT` and no other detail.

   All three triggers run as the **default Compute SA** (`534590904912-compute@developer...`), not
   `warehouse-deployer@`, even though `warehouse-deployer@` is the CI/CD identity everywhere else in
   this repo (WIF auth for GHA, the `deploy_service_account` / `ingest_service_account` Terraform
   vars). First attempt used `warehouse-deployer@` and `ugs-warehouse-deploy` failed cross-project
   (`PERMISSION_DENIED` on `ugs-warehouse-review-serving` in `ut-dnr-ugs-maps-prod`) — `infra/iam.tf`
   already grants `run.admin` + `serviceAccountUser` there, but only to whatever
   `var.build_service_account` in `infra/terraform.tfvars` names, and that's the default Compute SA
   (a prior, already-documented finding from 2026-07-10: `gcloud builds submit` with no
   `--service-account`, which is what GHA's `deploy.yml`/`viewer.yml` do, runs the build's *steps* as
   the project's default Compute SA regardless of which identity authenticated the API call). Matching
   the trigger's SA to that existing grant was the fix — no Terraform/IAM change needed.

3. First PR check came back `action_required`: the default `COMMENTS_ENABLED` comment-control gate
   wants a collaborator to comment `/gcbrun` on the PR before an untrusted-looking push actually
   builds. Expected, not a failure — comment `/gcbrun` and it runs.

4. `ugs-warehouse-pr-ci` proved green end-to-end on a real PR (#49). **Not yet done:** making it a
   required status check in branch protection for `main`, and dropping the GHA `test` / `Viewer CI`
   checks from required — planned after the deploy triggers below are proven too, so `main` doesn't
   go through a window of reduced coverage twice.

## Deploy + docs triggers

`ugs-warehouse-deploy` (→ `cloudbuild.yaml`, unscoped) and `ugs-warehouse-docs` (→
`cloudbuild-docs.yaml`, scoped to `docs/**`, `mkdocs.yml`, `docs-requirements.txt`) exist alongside
`ugs-warehouse-pr-ci`. No separate `ugs-warehouse-viewer` trigger — `cloudbuild.yaml` already builds
+ deploys both the public and review viewer, so a fourth trigger scoped to `viewer/**` would deploy
the public viewer twice per push; `cloudbuild-viewer.yaml` is unreferenced by any trigger as a
result and is a cleanup candidate if it stays that way.

GHA's `deploy.yml` / `viewer.yml` still run in parallel with these for now — deliberate overlap
while #60 proves the Cloud Build path out, removed when the GHA workflows are deleted (#49).
