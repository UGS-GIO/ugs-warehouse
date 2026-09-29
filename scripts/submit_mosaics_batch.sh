#!/usr/bin/env bash
# Submit the z17 geologic-map mosaic bake to Cloud Batch (ALL-6048): an ephemeral VM + disk that exist
# only for the run. The Cloud Run mosaics job has no disk for a statewide z17 tile tree.
#
# Defaults to a SCRATCH run: --quads writes only geologic-maps-24k-test-<editions>.pmtiles and no STAC
# item (pubs/geolmap_mosaics.py build()), so it cannot touch the live tier or catalog. The real
# statewide run needs --statewide and overwrites the live 24k object + item.
#
#   scripts/submit_mosaics_batch.sh                                  # scratch, 1 quad, 1h cap
#   scripts/submit_mosaics_batch.sh --quads "Park City East Quad,Heber City Quad"
#   scripts/submit_mosaics_batch.sh --statewide [--tag <sha>] [--yes]
#   scripts/submit_mosaics_batch.sh --print                          # show the job JSON, submit nothing
set -euo pipefail

# Also hardcoded in infra/batch/mosaics-job.json (SA, network, secret, SQL instance); change both together.
# The DB password is resolved from Secret Manager at run time and never appears in the job spec.
PROJECT=ut-dnr-ugs-backend-tools
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
REGION=us-west3   # = var.batch_region; colocated with the bucket and Cloud SQL
IMAGE="${IMAGE:-us-central1-docker.pkg.dev/${PROJECT}/ugs-warehouse/ugs-mosaics}"
TEMPLATE="$(cd "$(dirname "$0")/.." && pwd)/infra/batch/mosaics-job.json"

TAG=latest quads="Park City East Quad" quads_set=0 editions=current statewide=0 yes=0 print=0
while [ $# -gt 0 ]; do
  case "$1" in
    --statewide) statewide=1 ;;
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
if [ "$statewide" = 1 ] && [ "$quads_set" = 1 ]; then
  echo "--statewide and --quads conflict: pick the live statewide run or a scoped scratch run" >&2; exit 2
fi

args=(python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --maxzoom 17 --editions "$editions")
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

job="$(jq -n \
  --arg image "$digest" --arg timeout "$timeout" --arg run "$run" \
  --argjson cmd "$(printf '%s\n' "${args[@]}" | jq -R . | jq -s .)" \
  --slurpfile t "$TEMPLATE" '
  $t[0]
  | (.taskGroups[0].taskSpec.runnables[] | select(.displayName == "mosaics") | .container)
      |= (.imageUri = $image | .commands = $cmd)
  | .taskGroups[0].taskSpec.maxRunDuration = $timeout
  | .labels.run = $run')"
[ "$(jq --arg image "$digest" '[.taskGroups[0].taskSpec.runnables[] | select(.container.imageUri == $image)] | length' <<<"$job")" = 1 ] \
  || { echo "template has no single 'mosaics' runnable to fill" >&2; exit 1; }

if [ "$print" = 1 ]; then echo "$job"; exit 0; fi

echo "run:    ${run}"
echo "image:  ${digest}"
echo "cmd:    ${args[*]}"
if [ "$statewide" = 1 ]; then
  # Two statewide runs would race on the live object and its STAC item.
  active="$(gcloud batch jobs list --location "$REGION" \
    --filter='labels.run=full AND status.state:(QUEUED OR SCHEDULED OR RUNNING)' --format='value(name)')"
  [ -z "$active" ] || { echo "a statewide mosaics job is already active: ${active}" >&2; exit 1; }
fi
if [ "$statewide" = 1 ] && [ "$yes" != 1 ]; then
  [ -t 0 ] || { echo "--statewide needs --yes when stdin is not a terminal" >&2; exit 1; }
  read -r -p "This overwrites the LIVE 24k mosaic and its STAC item. Type 'statewide' to continue: " ok
  [ "$ok" = statewide ] || { echo "aborted" >&2; exit 1; }
fi

name="mosaics-${run}-$(date -u +%Y%m%d-%H%M%S)"
cfg="$(mktemp)"; trap 'rm -f "$cfg"' EXIT
echo "$job" > "$cfg"
gcloud batch jobs submit "$name" --location "$REGION" --config "$cfg"
echo "status: gcloud batch jobs describe ${name} --location ${REGION} --format='value(status.state)'"
