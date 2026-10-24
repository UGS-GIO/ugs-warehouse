#!/usr/bin/env bash
# Submit a geologic-map mosaic bake to Cloud Batch: an ephemeral VM + disk that exist only for the run.
# The 24k (z17) and 100k (z15) tiers build here; the Cloud Run mosaics job has neither the disk nor the
# time for their statewide tile trees. --scale picks the tier (default 24k).
#
# Defaults to a SCRATCH run: --quads writes only geologic-maps-<scale>-test-<editions>.pmtiles and
# .cog.tif, and no STAC item (pubs/geolmap_mosaics.py build()), so it cannot touch the live tier or
# catalog. The real statewide run needs --statewide and overwrites the live <scale> objects + item.
#
# --review writes to the login-gated review warehouse instead (the private review bucket under
# review/stac, served by the review app), still reading the COGs from the public bucket. Publishing a
# reviewed mosaic = the same run without --review.
#
#   scripts/submit_mosaics_batch.sh                                  # scratch, 1 quad, 1h cap
#   scripts/submit_mosaics_batch.sh --quads "Park City East Quad,Heber City Quad"
#   scripts/submit_mosaics_batch.sh --statewide --review             # full 24k into the review warehouse
#   scripts/submit_mosaics_batch.sh --statewide [--tag <sha>] [--yes]
#   scripts/submit_mosaics_batch.sh --scale 100k --statewide --review  # full 100k into the review warehouse
#   scripts/submit_mosaics_batch.sh --scale 100k --quads "<30x60 quad name>"
#   scripts/submit_mosaics_batch.sh --print                          # show the job JSON, submit nothing
set -euo pipefail

# Also hardcoded in infra/batch/mosaics-job.json (SA, network, secret, SQL instance); change both together.
# The DB password is resolved from Secret Manager at run time and never appears in the job spec.
PROJECT=ut-dnr-ugs-backend-tools
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
REGION=us-west3   # = var.batch_region; colocated with the bucket and Cloud SQL
# The review warehouse, as the _review ingest job targets it (cloudbuild.yaml _REVIEW_BUCKET/_SERVING_URL).
REVIEW_BUCKET=ut-dnr-ugs-maps-prod-review
REVIEW_BASE_URL=https://ugs-warehouse-review-serving-ufyuidl4mq-uc.a.run.app
PUBLIC_BUCKET=ut-dnr-ugs-maps-prod-public
IMAGE="${IMAGE:-us-central1-docker.pkg.dev/${PROJECT}/ugs-warehouse/ugs-mosaics}"
TEMPLATE="$(cd "$(dirname "$0")/.." && pwd)/infra/batch/mosaics-job.json"

TAG=latest scale=24k quads="Park City East Quad" quads_set=0 editions=current statewide=0 review=0 yes=0 print=0
while [ $# -gt 0 ]; do
  case "$1" in
    --statewide) statewide=1 ;;
    --review) review=1 ;;
    --scale) scale="${2:?--scale needs 24k or 100k}"; shift ;;
    --quads) quads="${2:?--quads needs a value}"; quads_set=1; shift ;;
    --editions) editions="${2:?--editions needs current|all}"; shift ;;
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    --yes) yes=1 ;;
    --print) print=1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$editions" in current|all) ;; *) echo "--editions must be current or all" >&2; exit 2 ;; esac
case "$scale" in 24k|100k) ;; *) echo "--scale must be 24k or 100k (500k builds on Cloud Run)" >&2; exit 2 ;; esac
if [ "$scale" = 100k ] && [ "$statewide" = 0 ] && [ "$quads_set" = 0 ]; then
  echo "a 100k scratch run needs --quads (the default quad is a 24k one)" >&2; exit 2
fi
if [ "$statewide" = 1 ] && [ "$quads_set" = 1 ]; then
  echo "--statewide and --quads conflict: pick the live statewide run or a scoped scratch run" >&2; exit 2
fi

# Pass the zoom explicitly so the run doesn't depend on the image's TIER_MAXZOOM (`latest` only
# moves on a main deploy).
case "$scale" in 24k) maxzoom=17 ;; 100k) maxzoom=15 ;; esac
args=(python -m ugs_warehouse.pubs.geolmap_mosaics --scale "$scale" --maxzoom "$maxzoom" --editions "$editions")
if [ "$statewide" = 1 ]; then
  run=full
  timeout=43200s
else
  run=scratch
  timeout=3600s
  args+=(--quads "$quads")
fi

# Pin the digest so a retry or a later push to :latest cannot change the code mid-run.
digest="$(gcloud artifacts docker images describe "${IMAGE}:${TAG}" \
  --format='value(image_summary.fully_qualified_digest)')"
[ -n "$digest" ] || { echo "cannot resolve ${IMAGE}:${TAG}" >&2; exit 1; }

target=live
[ "$review" = 1 ] && target=review
renv='{}'
if [ "$review" = 1 ]; then
  renv="$(jq -n --arg b "$REVIEW_BUCKET" --arg s "$PUBLIC_BUCKET" --arg u "$REVIEW_BASE_URL" \
    '{WAREHOUSE_BUCKET: $b, WAREHOUSE_SOURCE_BUCKET: $s, WAREHOUSE_PUBLIC_BASE_URL: $u,
      WAREHOUSE_STAC_PREFIX: "review/stac"}')"
fi

# Not `jq --args`: jq would parse the bake's own flags (-m, --scale) as jq options.
job="$(jq -n \
  --arg image "$digest" --arg timeout "$timeout" --arg run "$run" --arg target "$target" --arg scale "$scale" \
  --argjson renv "$renv" \
  --argjson cmd "$(printf '%s\n' "${args[@]}" | jq -R . | jq -s .)" \
  --slurpfile t "$TEMPLATE" '
  $t[0]
  | (.taskGroups[0].taskSpec.runnables[] | select(.displayName == "mosaics") | .container)
      |= (.imageUri = $image | .commands = $cmd)
  | (.taskGroups[0].taskSpec.runnables[] | select(.displayName == "mosaics") | .environment.variables)
      += $renv
  | .taskGroups[0].taskSpec.maxRunDuration = $timeout
  | .labels.run = $run | .labels.target = $target | .labels.scale = $scale')"
[ "$(jq --arg image "$digest" '[.taskGroups[0].taskSpec.runnables[] | select(.container.imageUri == $image)] | length' <<<"$job")" = 1 ] \
  || { echo "template has no single 'mosaics' runnable to fill" >&2; exit 1; }

if [ "$print" = 1 ]; then echo "$job"; exit 0; fi

echo "run:    ${run} -> ${target}"
echo "image:  ${digest}"
echo "cmd:    ${args[*]}"
if [ "$statewide" = 1 ]; then
  # Two statewide runs into the same warehouse would race on the STAC catalog refresh, whatever their
  # tiers, and on the object + item when the tier matches.
  # A live job submitted before the target label existed has no label at all.
  tfilter="labels.target=${target}"
  [ "$target" = live ] && tfilter="(labels.target=live OR -labels.target:*)"
  active="$(gcloud batch jobs list --location "$REGION" \
    --filter="labels.run=full AND ${tfilter} AND status.state:(QUEUED OR SCHEDULED OR RUNNING)" \
    --format='value(name)')"
  [ -z "$active" ] || { echo "a statewide ${target} mosaics job is already active: ${active}" >&2; exit 1; }
fi
if [ "$statewide" = 1 ] && [ "$target" = live ] && [ "$yes" != 1 ]; then
  [ -t 0 ] || { echo "--statewide needs --yes when stdin is not a terminal" >&2; exit 1; }
  read -r -p "This overwrites the LIVE ${scale} mosaic and its STAC item. Type 'statewide' to continue: " ok
  [ "$ok" = statewide ] || { echo "aborted" >&2; exit 1; }
fi

name="mosaics-${scale}-${run}-${target}-$(date -u +%Y%m%d-%H%M%S)"
cfg="$(mktemp)"; trap 'rm -f "$cfg"' EXIT
echo "$job" > "$cfg"
gcloud batch jobs submit "$name" --location "$REGION" --config "$cfg"
echo "status: gcloud batch jobs describe ${name} --location ${REGION} --format='value(status.state)'"
