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

`ugs-warehouse-deploy` (→ `cloudbuild.yaml`, **unscoped** — fires on every push to `main`, no
`includedFiles`) and `ugs-warehouse-docs` (→ `cloudbuild-docs.yaml`, scoped to `docs/**`,
`mkdocs.yml`, `docs-requirements.txt`) exist alongside `ugs-warehouse-pr-ci`.

**Update (#220 then #221, 2026-09):** `cloudbuild.yaml` no longer builds or deploys **any** viewer.
#220 moved the public one to `.github/workflows/firebase-hosting-merge.yml` (`docs/DEPLOY.md` §5);
#221 moved the review bundle out too, into `cloudbuild-review-viewer.yaml`, and deleted the old
`cloudbuild-viewer.yaml`. `build-viewer`, `build-viewer-review`, `deploy-viewer-review` and the two
`resolve-*-url` steps that only fed them are gone from `cloudbuild.yaml`.

That is only half the fix. **`ugs-warehouse-deploy` is still unscoped**, so a viewer-only change
still fires the full image-build pipeline — it now just does no viewer work while doing it. And
`cloudbuild-review-viewer.yaml` has **no trigger yet**, so nothing rebuilds `/review/viewer/` until
one exists. Both are #222.

**GHA is back, partially (#220).** #49 deleted all GHA workflows in favor of Cloud Build for
everything; #220 reintroduced `.github/workflows/` for exactly the two things Cloud Build
structurally cannot do — see the trigger inventory below. Cloud Build still owns every other build
and deploy in this repo (images, Cloud Run services, the review viewer, docs, CI).

## Applying the scope (#222)

`infra/cloudbuild-triggers.tf` declares `ugs-warehouse-deploy` and `ugs-warehouse-review-viewer`.
It is not applied — see the inventory below for what is actually live. To apply:

```bash
# the deploy trigger already exists — import it, or the create 409s
tofu import 'google_cloudbuild_trigger.deploy[0]' \
  projects/ut-dnr-ugs-backend-tools/locations/us-central1/triggers/ugs-warehouse-deploy
tofu apply    # re-scopes deploy, creates ugs-warehouse-review-viewer
```

Set `build_repository` and `trigger_service_account` in `terraform.tfvars` first. The triggers live
in the BUILD project, not `var.project_id`, so the deploy SA needs `roles/cloudbuild.builds.editor`
there — a cross-project grant, of exactly the kind `just check-grants` (#223) now enumerates.

## Trigger inventory — what owns what (#224)

Every trigger below was found by `gcloud builds triggers describe`. **All five are now declared in
`infra/cloudbuild-triggers.tf`** (transcribed from this table), plus `ugs-warehouse-review-viewer`
which does not exist yet — but declaring is not applying: they are guarded on `build_repository` +
the two SA variables, none is imported, and no `tofu apply` has run. Treat the triggers as
console-owned until #222 closes.

The table below stays the authority on what is actually deployed; the `.tf` is a proposal until
imported. The two preview triggers run as `ugs-warehouse-preview-build@` rather than the Compute SA,
so the file carries a separate `preview_trigger_service_account` — a preview builds unmerged branch
code and must stay the least-trusted identity.

Import each before the first apply, or every create 409s:

```bash
cd infra
B=projects/ut-dnr-ugs-backend-tools/locations/us-central1/triggers
for r in deploy:ugs-warehouse-deploy pr_ci:ugs-warehouse-pr-ci docs:ugs-warehouse-docs \
         viewer_preview:ugs-warehouse-viewer-preview tiles_preview:ugs-warehouse-tiles-preview; do
  tofu import "google_cloudbuild_trigger.${r%%:*}[0]" "$B/${r##*:}"
done
tofu plan    # <- THE ACCEPTANCE TEST, read it before applying
```

**`tofu plan` after the imports is what proves the transcription.** The four transcribed triggers —
`pr_ci`, `docs`, `viewer_preview`, `tiles_preview` — must show **no changes**. They were copied from
`triggers describe` by hand and nothing else checks that; a diff on any of them means the `.tf` is
wrong, not the live trigger, and applying would overwrite a working trigger with a bad field
(`included_files`, `comment_control`, or the service account). Fix the file to match, re-plan.

Only two changes are expected:

| resource | expected plan |
|---|---|
| `deploy` | **update** — `included_files` added, the whole point of #222 |
| `review_viewer` | **create** — does not exist yet |
| the other four | **no changes** |

```bash
tofu apply   # only once the plan reads as above
```

All five Cloud Build triggers live in `ut-dnr-ugs-backend-tools` (the build project), on the
2nd-gen GitHub connection `ugs-warehouse-github` (repository resource `ugs-warehouse`) — itself
console/CLI-created (§ One-time setup above), not Terraform-managed either.

| trigger | config | event | path scope | service account |
|---|---|---|---|---|
| `ugs-warehouse-pr-ci` | `cloudbuild-ci.yaml` | PR, any branch | none (all paths) | default Compute SA |
| `ugs-warehouse-deploy` | `cloudbuild.yaml` | push to `main` | **none — unscoped** (#222) | default Compute SA |
| `ugs-warehouse-docs` | `cloudbuild-docs.yaml` | push to `main` | `docs/**`, `mkdocs.yml`, `docs-requirements.txt` | default Compute SA |
| `ugs-warehouse-viewer-preview` | `cloudbuild-viewer-preview.yaml` | PR to `main` | `viewer/**` | `ugs-warehouse-preview-build@` |
| `ugs-warehouse-tiles-preview` | `cloudbuild-service-preview.yaml` | PR to `main` | `tiles/**`, `api/**`, `src/**` | `ugs-warehouse-preview-build@` |
| `ugs-warehouse-review-viewer` | `cloudbuild-review-viewer.yaml` | push to `main` | `viewer/**` | — | **DOES NOT EXIST YET (#222).** The config landed in #221; until the trigger is created, nothing rebuilds the review viewer bundle.

`default Compute SA` = `534590904912-compute@developer.gserviceaccount.com` — see the "why not
`warehouse-deployer@`" note above (§ One-time setup, point 2); that finding still holds.

Two more things run builds but are **not** Cloud Build triggers — GitHub Actions submitting to
Cloud Build on events the trigger config can't express:

| workflow | fires on | submits | runs as |
|---|---|---|---|
| `.github/workflows/preview-cleanup.yml` | PR `closed` (no trigger equivalent for this event) | `cloudbuild-preview-cleanup.yaml` | `ugs-warehouse-preview-build@` (WIF, no key) |
| `.github/workflows/firebase-hosting-merge.yml` / `-pull-request.yml` | push to `main` / PR, `viewer/**` | nothing — deploys directly via `firebase-tools`, no Cloud Build involved | Firebase service-account key (`FIREBASE_SERVICE_ACCOUNT_UT_DNR_UGS_MAPS_PROD` repo secret, see `docs/DEPLOY.md` §5) |

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
  the Firebase Hosting *site* (`infra/firebase.tf`). Apply is two-box (personal authors `.tf`, work
  box runs `tofu plan`/`apply` impersonating `warehouse-deploy@`) — see `infra/README.md`.
- **Console/CLI, not tofu**: all 5 Cloud Build triggers (table above) — now *declared* in
  `infra/cloudbuild-triggers.tf` but not imported or applied — the GitHub host connection,
  `warehouse-deploy@`'s `roles/firebasehosting.admin` grant (added out-of-band for #220, same gap
  #223 already tracks for other cross-project grants).
- **GitHub repo settings, not this repo's code**: the required secret
  `FIREBASE_SERVICE_ACCOUNT_UT_DNR_UGS_MAPS_PROD` and the optional variables
  `VITE_FEATURES_BASE`/`VITE_TILES_BASE` (documented in `docs/DEPLOY.md` §5, set via `gh secret
  set`/`gh variable set` or the GitHub UI — GitHub has no Terraform-adjacent way to declare these
  short of the `github` Terraform provider, which this repo doesn't use).

None of the console-only items above are wrong to be console-only — some structurally can't move
(GitHub Actions events, Firebase credentials Cloud Build has no role for). The gap #224 flags is
that this was previously discoverable only by running the commands above or reading PR bodies; this
section is that write-down. Turning any of the "console/CLI" row into an actual `resource` block
(import + `tofu apply`) is separate, higher-risk follow-up work — #222 for the triggers specifically.

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
