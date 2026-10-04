#!/usr/bin/env bash
# Publications "full refresh" orchestrator — chains the pub jobs in order so the ops console can run
# the whole thing with one button instead of a 3-step dance. Runs as the `ugs-pubs-pipeline` Cloud
# Run Job (server-side, survives the operator closing the tab).
#
#   thumbs (loop until covers/contents/fulltext sidecars stop growing) -> threed (convert new 3D pubs)
#     -> vectors (extract new pubs' GIS layers) -> prune (drop items the source no longer lists)
#     -> pubs-ingest (bind + corpus) -> pubs-fts (all-pub BM25 index)
#
# Its runtime SA needs roles/run.developer (execute the sub-jobs) + actAs on their runtime SA.
set -uo pipefail

REGION="${REGION:-us-central1}"
BUCKET="${WAREHOUSE_BUCKET:-ut-dnr-ugs-maps-prod-public}"
THUMBS_JOB="${THUMBS_JOB:-ugs-pubs-thumbs}"
THREED_JOB="${THREED_JOB:-ugs-pubs-threed}"
VECTORS_JOB="${VECTORS_JOB:-ugs-pubs-vectors}"
PRUNE_JOB="${PRUNE_JOB:-ugs-pubs-prune}"
INGEST_JOB="${INGEST_JOB:-ugs-pubs-ingest}"
FTS_JOB="${FTS_JOB:-ugs-pubs-fts}"
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

# Convert any new 3D pubs (gdb + .mapx → GeoParquet-3D + glTF + classes sidecar). skip-existing, so
# this only does work for newly-added 3D pubs; the ingest below binds the assets presence-driven.
run_job "$THREED_JOB"

# Extract the layers of any new pub with a GIS zip (skip-existing); the ingest below binds them.
run_job "$VECTORS_JOB"

# Delete items the source no longer lists, so the ingest's catalog refresh drops them.
run_job "$PRUNE_JOB"

# Bind covers/contents/volume + 3D assets into STAC + aggregate the search corpus + refresh the catalog.
run_job "$INGEST_JOB"

# Build the all-pub full-text-search DuckDB index from the fulltext sidecars the thumbs pass wrote.
run_job "$FTS_JOB"

echo "[pipeline] ✓ publications refresh complete"
