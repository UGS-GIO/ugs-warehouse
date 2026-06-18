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
