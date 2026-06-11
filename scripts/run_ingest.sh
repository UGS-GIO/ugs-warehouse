#!/usr/bin/env bash
set -e

# Fetch password from GCP Secret Manager
DB_PASS=$(gcloud secrets versions access latest --secret="dbt-prod-db-password" --project="ut-dnr-ugs-backend-tools")

# Infrastructure and Pipeline configuration
export POSTGRES_DSN="postgresql://schema_owner:${DB_PASS}@127.0.0.1:5433/mapping-db"
export DUCKLAKE_CATALOG_DSN="$POSTGRES_DSN"
export SOURCE_BACKEND="postgres"

# Load additional project-specific env vars if they exist
[ -f .env ] && source .env

# Execute requested ingest command
python -m ugs_warehouse.vector.ingest "$@"
