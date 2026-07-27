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

3. First PR check came back `action_required`: `gcloud builds triggers create github` defaults to
   `--comment-control=COMMENTS_ENABLED`, which holds *every* PR build until a collaborator comments
   `/gcbrun`. The trigger was recreated with `COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY` (the
   `update` subcommand 400s on 2nd-gen triggers — delete + recreate), so org-member PRs now build
   automatically and only outside contributors are gated.

4. `ugs-warehouse-pr-ci` proved green end-to-end on a real PR, and all three triggers were proven on
   real pushes before the GHA workflows were deleted in #49.

## Deploy + docs triggers

`ugs-warehouse-deploy` (→ `cloudbuild.yaml`, unscoped) and `ugs-warehouse-docs` (→
`cloudbuild-docs.yaml`, scoped to `docs/**`, `mkdocs.yml`, `docs-requirements.txt`) exist alongside
`ugs-warehouse-pr-ci`. No separate `ugs-warehouse-viewer` trigger — `cloudbuild.yaml` already builds
+ deploys both the public and review viewer, so a fourth trigger scoped to `viewer/**` would deploy
the public viewer twice per push.

`cloudbuild-viewer.yaml` is therefore unreferenced by any trigger. Do NOT read that as dead config:
it was the viewer *fast path* (Vite build + rsync, no image builds), so with only `cloudbuild.yaml`
firing, a one-line viewer change now rebuilds every service image. That is a real cost, and the
choice is to either restore a `viewer/**`-scoped trigger with the viewer steps removed from
`cloudbuild.yaml`, or accept slower viewer deploys and delete the file. Undecided.

GHA is gone — #49 deleted all five workflows and `.github/` with them. Cloud Build is the only thing
building or deploying this repo.

## Checks are advisory, not blocking

There is **no branch protection on `main`, and none can be added**: classic protection and rulesets
both require a paid plan for private repos, and `UGS-GIO` is on GitHub's free tier (both API
endpoints return 403). A red `ugs-warehouse-pr-ci` is visible and does not prevent a merge.

This is not a Cloud Build limitation and was equally true of the GHA checks that preceded it —
nothing has ever gated a merge in this repo. It resolves for free if the repo goes public.

If protection does become available, the required check must be named **exactly**
`ugs-warehouse-pr-ci (ut-dnr-ugs-backend-tools)`, parenthetical included. A bare `ugs-warehouse-pr-ci`
matches no check run, and a required check that never appears blocks every PR permanently while
looking like a broken trigger.
