# Standard tasks
default:
    @just --list

ingest:
    python -m ugs_warehouse.ingest --all

refresh:
    python -m scripts.refresh_stac

test:
    pytest

# Styling: hermetic style/restyle tests (no GCS, no DB, no perms)
test-styles:
    pytest tests/test_styles.py tests/test_restyle.py

# Rebind ugs-styles renders into STAC, no reingest (see STYLING.md §10)
restyle:
    python -m ugs_warehouse.restyle

restyle-all:
    python -m ugs_warehouse.restyle --collection all

restyle-dry:
    python -m ugs_warehouse.restyle --dry-run

# diagnose binding: which items styled / asset-miss / orphan styles (writes nothing)
restyle-report:
    python -m ugs_warehouse.restyle --report

proxy:
    cloud-sql-proxy ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5433

# One-time infra provisioning (Pub/Sub topic + push subscription + run.invoker). Idempotent —
# run once after the first deploy, or if the service/topic/sub names change. NOT in the build.
provision:
    bash scripts/provision.sh

# DuckLake maintenance — expire old snapshots + compact small parquet + GC orphaned files. Keeps the
# append-only catalog bounded/fast. Run ~weekly. In prod = the `ugs-warehouse-ducklake-maintain` job.
maintain *ARGS:
    python -m ugs_warehouse.vector.maintain {{ARGS}}

# Build the all-pub full-text-search DuckDB (BM25 over every pub's body → CDN).
# In prod this is the `ugs-pubs-fts` Cloud Run job (ops console button); local run needs GCS ADC.
fts:
    python -m ugs_warehouse.pubs.fts

# Cross-boundary IAM grant preflight (#223, docs/DEPLOY.md §6). Read-only gcloud calls — no
# impersonation, no tofu state needed. Fails (exit 1) if anything in the table is missing.
check-grants:
    python3 scripts/check_grants.py

# Run the IAP review serving app locally (streams WAREHOUSE_BUCKET read-only). PORT defaults to 8080.
serve bucket="ut-dnr-ugs-maps-prod-review":
    WAREHOUSE_BUCKET={{bucket}} python -m ugs_warehouse.serve

# --- infra/ (OpenTofu: review/private serving substrate) -----------------------------------------
# tf-check runs anywhere (no creds). init/plan/apply need GCP perms → WORK BOX only ([[two-box]]).
# All recipes run inside infra/. Fill infra/terraform.tfvars first (see terraform.tfvars.example).

# fmt + validate — safe on any box, no GCP creds needed.
tf-check:
    cd infra && tofu fmt && tofu init -backend=false >/dev/null && tofu validate

tf-init:
    cd infra && tofu init

# Plan to a file so apply is exactly what you reviewed. REVIEW THE DIFF: creates only, zero prod destroys.
tf-plan:
    cd infra && tofu plan -out plan.tfplan

tf-apply:
    cd infra && tofu apply plan.tfplan

tf-output:
    cd infra && tofu output

# Serve the built viewer exactly as Firebase Hosting will (SPA rewrites + cache headers).
# Build first: cd viewer && npm run build
viewer-preview:
    firebase emulators:start --only hosting --project demo-ugs
