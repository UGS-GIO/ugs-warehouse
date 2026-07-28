#!/bin/sh
# Re-derive the collection list from the live STAC catalog, then hand off to featureserv.
#
# This is the freshness mechanism (#89): the service is scale-to-zero and near-every real request
# is already a cold start, so "collections as of the last visit" costs one catalog scan here rather
# than a full image rebuild. gen_db soft-fails and writes atomically, so an unreachable CDN leaves
# the image-baked snapshot in place and we still start. Nothing listens on :9000 until this
# returns, so the scan runs against Cloud Run's container startup budget (240s default probe).
set -u

python3 /app/gen_db.py --out "${DUCKDBFS_DATABASE_PATH}" \
  || echo "[entrypoint] gen_db failed — serving the image-baked snapshot"

exec /duckdb_featureserv "$@"
