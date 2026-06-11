#!/usr/bin/env bash
# Cloud Run entrypoint — the deploy analog of run_ingest.sh.
#
# On Cloud Run there is no cloud_sql_proxy: mapping-db is reached over the Cloud SQL
# unix socket that `--set-cloudsql-instances` mounts at /cloudsql/<CLOUDSQL_INSTANCE>,
# and the DB password is injected from Secret Manager via `--set-secrets DB_PASS=...`.
# This builds POSTGRES_DSN / DUCKLAKE_CATALOG_DSN from those, then runs the ingest CLI.
#
# Env supplied by the job/service deploy:
#   CLOUDSQL_INSTANCE  project:region:instance  (also passed to --set-cloudsql-instances)
#   DB_PASS            from --set-secrets (Secret Manager)
#   DB_NAME, DB_USER   from --set-env-vars (default seamlessgeolmap / schema_owner)
#   plus DUCKLAKE_DATA_PATH, WAREHOUSE_* as needed
set -euo pipefail

: "${CLOUDSQL_INSTANCE:?set via --set-env-vars (project:region:instance)}"
: "${DB_PASS:?set via --set-secrets=DB_PASS=<secret>:latest}"

export POSTGRES_DSN="host=/cloudsql/${CLOUDSQL_INSTANCE} dbname=${DB_NAME:-seamlessgeolmap} user=${DB_USER:-schema_owner} password=${DB_PASS}"
export DUCKLAKE_CATALOG_DSN="${POSTGRES_DSN}"
export SOURCE_BACKEND="${SOURCE_BACKEND:-postgres}"

exec python -m ugs_warehouse.vector.ingest "$@"
