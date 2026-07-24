# CI on Cloud Build (not GitHub Actions)

GitHub Actions runners depend on GHA billing; when that lapses, every check goes red and nothing
merges. Cloud Build runs under the GCP project's own billing (same place deploys already run), so
moving CI there decouples it — the same pattern `ugs-ucrc-asset-management-v2` uses (all CI/CD on
Cloud Build, no GHA workflows).

## What runs

[`cloudbuild-ci.yaml`](../cloudbuild-ci.yaml) — PR validation, test-only (no push/deploy):

- **backend** (`python:3.11`): `pip install -e ".[dev]"` → `ruff check .` → `pytest -q`
- **viewer** (`node:20`, `dir: viewer`): `npm ci` → `tsc --noEmit` → `eslint .` → `vitest run`

Both steps run in parallel (`waitFor: ["-"]`); either failing fails the build → the PR check goes red.

## One-time setup (needs GCP perms — run on the work box)

1. **Connect the repo to Cloud Build** (2nd-gen). Console → Cloud Build → Repositories → *Connect
   repository* → GitHub → install the Cloud Build GitHub app on `UGS-GIO/ugs-warehouse`. This is
   what posts build status back onto PRs.

2. **Create the PR trigger** pointing at this config:

   ```bash
   gcloud builds triggers create github \
     --name=ugs-warehouse-pr-ci \
     --region=us-central1 \
     --repo-name=ugs-warehouse --repo-owner=UGS-GIO \
     --pull-request-pattern='^.*$' \
     --build-config=cloudbuild-ci.yaml \
     --project=ut-dnr-ugs-backend-tools
   ```

   (Or the 2nd-gen form with `--repository=projects/.../connections/<conn>/repositories/ugs-warehouse`.)

3. Make the resulting **`ugs-warehouse-pr-ci`** status check *required* in the branch protection for
   `main`, and drop the GHA `test` / `Viewer CI` checks from required once this is green.

## Migrating the rest (follow-up)

`deploy.yml` and `viewer.yml` today only authenticate + `gcloud builds submit` from a GHA runner, so
they're also GHA-billing-blocked. They can move to **push triggers** on `main` the same way (build
config `cloudbuild.yaml` / `cloudbuild-viewer.yaml`), retiring the GHA workflows entirely (keep
`dependabot.yml`). Do this after the PR trigger is confirmed working.
