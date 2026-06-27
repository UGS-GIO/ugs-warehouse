#!/usr/bin/env bash
# Publications "full refresh" orchestrator — chains the pub jobs in order so the ops console can run
# the whole thing with one button instead of a 3-step dance. Runs as the `ugs-pubs-pipeline` Cloud
# Run Job (server-side, survives the operator closing the tab).
#
#   thumbs (loop until covers/contents/search sidecars stop growing) -> pubs-ingest (bind + corpus)
#
# Its runtime SA needs roles/run.developer (execute the sub-jobs) + actAs on their runtime SA.
set -uo pipefail

REGION="${REGION:-us-central1}"
BUCKET="${WAREHOUSE_BUCKET:-ut-dnr-ugs-maps-prod-public}"
THUMBS_JOB="${THUMBS_JOB:-ugs-pubs-thumbs}"
INGEST_JOB="${INGEST_JOB:-ugs-pubs-ingest}"
MAX_THUMB_ROUNDS="${MAX_THUMB_ROUNDS:-8}"

run_job() {  # execute a Cloud Run Job and wait for it; a timed-out task is fine (we loop/check)
  echo "[pipeline] ▶ executing $1"
  gcloud run jobs execute "$1" --region "$REGION" --wait || echo "[pipeline] $1 returned non-zero (continuing)"
}

sidecar_count() {  # total pub derivative sidecars in the bucket = progress signal across thumbs runs
  gcloud storage ls "gs://${BUCKET}/pubs/thumbs/**" "gs://${BUCKET}/pubs/contents/**" 2>/dev/null | wc -l
}

echo "[pipeline] === publications full refresh ==="

# Thumbs is sharded with a 1h task timeout, so one execution may not finish everything. skip-existing
# makes each run resume; loop until the sidecar count stops growing (converged) or we hit the cap.
prev=-1
for round in $(seq 1 "$MAX_THUMB_ROUNDS"); do
  echo "[pipeline] thumbs round $round/$MAX_THUMB_ROUNDS"
  run_job "$THUMBS_JOB"
  count="$(sidecar_count)"
  echo "[pipeline] sidecars so far: $count (was $prev)"
  if [ "$count" -le "$prev" ]; then echo "[pipeline] thumbs converged"; break; fi
  prev="$count"
done

# Bind covers/contents/volume into STAC + aggregate the search corpus + refresh the catalog.
run_job "$INGEST_JOB"

echo "[pipeline] ✓ publications refresh complete"
