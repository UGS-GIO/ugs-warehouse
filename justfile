# Standard tasks
default:
    @just --list

ingest:
    python -m ugs_warehouse.ingest --all

refresh:
    python -m scripts.refresh_stac

test:
    pytest

proxy:
    cloud-sql-proxy ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5433
