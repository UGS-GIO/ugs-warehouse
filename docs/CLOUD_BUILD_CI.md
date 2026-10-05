# CI on Cloud Build (not GitHub Actions)

GitHub Actions runners depend on GHA billing; when that lapses, every check goes red and nothing
merges. Cloud Build runs under the GCP project's own billing (same place deploys already run), so
moving CI there decouples it — the same pattern `ugs-ucrc-asset-management-v2` uses (all CI/CD on
Cloud Build, no GHA workflows).

## What runs

[`cloudbuild-ci.yaml`](https://github.com/UGS-GIO/ugs-warehouse/blob/main/cloudbuild-ci.yaml) — PR validation, test-only (no push/deploy):

- **backend** (`python:3.11`): `pip install -e ".[dev]"` → `ruff check .` → `pytest -q`
- **docs-links** (`lycheeverse/lychee`): every relative Markdown link in the repo resolves (offline)
- **viewer** (`node:22`, `dir: viewer`): `npm ci` → `tsc --noEmit` → `eslint .` → `vitest run` → `npm run build`

All steps run in parallel (`waitFor: ["-"]`); any failing fails the build → the PR check goes red.

## One-time setup

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
   `--service-account`, which is what the since-removed GHA `deploy.yml`/`viewer.yml` did, runs the build's *steps* as
   the project's default Compute SA regardless of which identity authenticated the API call). Matching
   the trigger's SA to that existing grant was the fix — no Terraform/IAM change needed.

3. First PR check came back `action_required`: `gcloud builds triggers create github` defaults to
   `--comment-control=COMMENTS_ENABLED`, which holds *every* PR build until a collaborator comments
   `/gcbrun`. The trigger was recreated with `COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY` (the
   `update` subcommand 400s on 2nd-gen triggers — delete + recreate), so org-member PRs now build
   automatically and only outside contributors are gated.

4. `ugs-warehouse-pr-ci` proved green end-to-end on a real PR, and all three triggers were proven on
   real pushes before the old GHA workflows were deleted.

## Deploy + docs triggers

`ugs-warehouse-deploy` (→ `cloudbuild.yaml`, production, on main, scoped by `deploy_included_files`),
`ugs-warehouse-dev` (→ `cloudbuild-dev.yaml`, the develop environment, on develop, scoped by
`dev_included_files` in `infra/cloudbuild-triggers.tf`) and `ugs-warehouse-docs`
(→ `cloudbuild-docs.yaml`, scoped to `docs/**`, `mkdocs.yml`, `docs-requirements.txt`,
`cloudbuild-docs.yaml`) exist alongside `ugs-warehouse-pr-ci`.

`cloudbuild.yaml` no longer builds or deploys **any** viewer — the public one deploys via
`.github/workflows/firebase-hosting-merge.yml` and the dev one via `firebase-hosting-develop.yml`
(`docs/DEPLOY.md` §5), the review bundle via
`cloudbuild-review-viewer.yaml` on its own trigger (`ugs-warehouse-review-viewer`). Both
`ugs-warehouse-deploy`'s path scope and the review-viewer trigger's existence are now applied —
a viewer-only change no longer fires the backend image-build pipeline, and `/review/viewer/`
rebuilds on its own trigger.

**GHA is back, partially.** All GHA workflows were deleted at one point in favor of Cloud Build for
everything, then `.github/workflows/` was reintroduced for exactly the two things Cloud Build
structurally cannot do — see the trigger inventory below. Cloud Build still owns every other build
and deploy in this repo (images, Cloud Run services, the review viewer, docs, CI).

## Applying the scope

`infra/cloudbuild-triggers.tf` declares and now **manages** all six triggers — `ugs-warehouse-deploy`
and `ugs-warehouse-review-viewer` are applied; the five pre-existing ones are imported.

Set `build_repository`, `trigger_service_account`, and `preview_trigger_service_account` in
`terraform.tfvars` (examples in `terraform.tfvars.example`). The triggers live in the BUILD
project, not `var.project_id`, so the deploy SA needs, cross-project, all granted in `iam.tf`:

- `roles/cloudbuild.builds.editor` on `build_project` (like the Firebase grant)
- `roles/iam.serviceAccountUser` on `trigger_service_account` and `preview_trigger_service_account`
  — creating or updating a trigger with a given runtime SA requires actAs on that SA;
  `cloudbuild.builds.editor` alone 403s with "does not have impersonation permission on the trigger
  service account specified"
- `roles/iam.securityReviewer` on those same two SAs (read-only, resource-scoped) — needed for every
  `tofu plan`/`apply` to refresh the actAs grants above, or it 403s with nothing actually changed

A first-time import of a not-yet-managed trigger still 409s on create:

```bash
cd infra
B=projects/ut-dnr-ugs-backend-tools/locations/us-central1/triggers
tofu import "google_cloudbuild_trigger.NAME[0]" "$B/ugs-warehouse-NAME"
tofu plan    # confirms no unexpected diff before applying
tofu apply
```

## Trigger inventory — what owns what

Every trigger below was found by `gcloud builds triggers describe` and is now managed by
`infra/cloudbuild-triggers.tf`. The two preview triggers run as `ugs-warehouse-preview-build@`
rather than the Compute SA — a preview builds unmerged branch code and must stay the
least-trusted identity, hence the separate `preview_trigger_service_account` variable. The basemap
trigger runs as `ugs-basemap-build@`, which can write only under `basemap/` in the public bucket
(`basemap_trigger_service_account`; grants in `infra/basemap-refresh.tf`).

All Cloud Build triggers live in `ut-dnr-ugs-backend-tools` (the build project), on the
2nd-gen GitHub connection `ugs-warehouse-github` (repository resource `ugs-warehouse`) — itself
console/CLI-created (§ One-time setup above), not Terraform-managed either.

| trigger | config | event | path scope | service account |
|---|---|---|---|---|
| `ugs-warehouse-pr-ci` | `cloudbuild-ci.yaml` | PR, any branch | none (all paths) | default Compute SA |
| `ugs-warehouse-deploy` | `cloudbuild.yaml` | push to `main` | scoped to backend paths (`deploy_included_files`) | default Compute SA |
| `ugs-warehouse-dev` | `cloudbuild-dev.yaml` | push to `develop` | read-side service paths (`dev_included_files`) | default Compute SA |
| `ugs-warehouse-docs` | `cloudbuild-docs.yaml` | push to `main` | `docs/**`, `mkdocs.yml`, `docs-requirements.txt`, `cloudbuild-docs.yaml` | default Compute SA |
| `ugs-warehouse-viewer-preview` | `cloudbuild-viewer-preview.yaml` | PR to `main` or `develop` | `viewer/**` | `ugs-warehouse-preview-build@` |
| `ugs-warehouse-tiles-preview` | `cloudbuild-service-preview.yaml` | PR to `main` or `develop` | `tiles/**` | `ugs-warehouse-preview-build@` |
| `ugs-warehouse-review-viewer` | `cloudbuild-review-viewer.yaml` | push to `main` | `viewer/**` | default Compute SA |
| `ugs-warehouse-basemap` | `cloudbuild-basemap.yaml` | Pub/Sub `ugs-warehouse-basemap` (monthly Cloud Scheduler, or by hand) | n/a | `ugs-basemap-build@` |

`default Compute SA` = `534590904912-compute@developer.gserviceaccount.com` — see the "why not
`warehouse-deployer@`" note above (§ One-time setup, point 2); that finding still holds.

Two more things run builds but are **not** Cloud Build triggers — GitHub Actions submitting to
Cloud Build on events the trigger config can't express:

| workflow | fires on | submits | runs as |
|---|---|---|---|
| `.github/workflows/preview-cleanup.yml` | PR `closed` (no trigger equivalent for this event) | `cloudbuild-preview-cleanup.yaml` | `ugs-warehouse-preview-build@` (WIF, no key) |
| `.github/workflows/firebase-hosting-merge.yml` / `-develop.yml` / `-pull-request.yml` | push to `main` (live site) / push to `develop` (dev site) / PR, `viewer/**` | nothing — deploys directly via `firebase-tools`, no Cloud Build involved | Firebase service-account key (`FIREBASE_SERVICE_ACCOUNT_UT_DNR_UGS_MAPS_PROD` repo secret, see `docs/DEPLOY.md` §5) |

`-pull-request.yml` also upserts the single PR comment carrying **both** preview links — the public
Firebase channel it just deployed, and the IAP review preview from `cloudbuild-viewer-preview.yaml`
(whose URL is deterministic, `…/viewer/pr-<n>/`). It replaced `preview-comment.yml`, which posted a
second comment for the review link alone.

Both previews are always built. They cannot be reduced to one, and the review one cannot be
path-filtered away: it is the same tree built against the review catalog, and `IS_REVIEW` branches
inside shared files (`item-detail.tsx`, `browse.tsx`, `app.tsx`, `stac.ts`, …), so a "review code
changed?" filter would skip the review preview on precisely the PRs that alter review rendering.

**What's Terraform-owned vs. not**, the actual current boundary:

- **Tofu** (`infra/*.tf`): buckets, Cloud Run services, service accounts + their IAM bindings, IAP,
  the Firebase Hosting *site* (`infra/firebase.tf`), and now all 6 Cloud Build triggers
  (`infra/cloudbuild-triggers.tf`). Apply is two-box (personal authors `.tf`, work box runs
  `tofu plan`/`apply` impersonating `warehouse-deploy@`) — see `infra/README.md`.
- **Console/CLI, not tofu**: the GitHub host connection (`ugs-warehouse-github`),
  `warehouse-deploy@`'s `roles/firebasehosting.admin` grant (added out-of-band, same class of
  cross-project grant as the trigger ones above but for Firebase Hosting instead of Cloud Build).
- **GitHub repo settings, not this repo's code**: the required secret
  `FIREBASE_SERVICE_ACCOUNT_UT_DNR_UGS_MAPS_PROD` and the optional variables
  `VITE_FEATURES_BASE`/`VITE_TILES_BASE` (documented in `docs/DEPLOY.md` §5, set via `gh secret
  set`/`gh variable set` or the GitHub UI — GitHub has no Terraform-adjacent way to declare these
  short of the `github` Terraform provider, which this repo doesn't use).

None of the console-only items above are wrong to be console-only — some structurally can't move
(GitHub Actions events, Firebase credentials Cloud Build has no role for).

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
