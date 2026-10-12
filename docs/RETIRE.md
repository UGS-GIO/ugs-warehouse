# Retiring a serving topic

Removing a topic used to be four manual steps in an order nobody wrote down. It is now one
command:

```bash
python -m ugs_warehouse.vector.retire --topic hazards.hazards_qfaults_current --dry-run
python -m ugs_warehouse.vector.retire --topic hazards.hazards_qfaults_current
```

`--dry-run` prints the DuckLake table, every object that would go, and every file that mentions
the topic. It writes nothing, refreshes nothing, and does not prompt.

Needs `DUCKLAKE_CATALOG_DSN` (mapping-db) and write access to `WAREHOUSE_BUCKET`.

## From the ops console

The Data tab links to **Retire a topic**, which runs the same CLI as a Cloud Run job
(`ugs-warehouse-retire`) so the operator needs neither a `cloud_sql_proxy` nor bucket credentials.

Step 1 starts a `--dry-run` and shows that execution's output. Step 2 needs the topic typed back
before the button works, and the view re-checks it, because a disabled button is not a control.
The operator's IAP email rides along as `RETIRE_REQUESTED_BY` and the CLI prints it, so the run's
own log stream carries who asked — the console stores nothing (its SQLite is ephemeral).

The job exists on its own rather than as an argument override on the maintenance job, so that
retirements keep their own execution history and log stream in the console.

## What it does, in order

1. **Reference scan, then a typed confirmation.** `git grep` for the topic stem (tests excluded)
   and a lookup in the ugs-styles manifest, both printed. The scan is advisory: nothing declares
   which item ids a consumer binds (#238), so it reports docstring examples as readily as real
   bindings, and the consumer that matters most lives in another repo. The gate is typing the
   topic name back, or `--yes` for automation.
2. **DuckLake.** `maintain.drop_table` reads the parquet the catalog references, drops the table,
   then deletes those files. Reading first is not optional: a dropped table cannot be listed.
3. **Artifacts.** Every object under `<prefix>/<stem>/` for the archive parquet (latest plus every
   dated copy), PMTiles, thumbnails, and the STAC item.
4. **Catalog.** `refresh_catalog()` rebuilds `catalog.json`, the schema collection and the item
   index from what is left in the bucket.

## What survives, and why

| | |
|---|---|
| Metadata override (`warehouse/overrides/<stem>.json`) | Hand-authored, not reproducible from source. A re-ingest of the same topic recovers its curation. `--purge-overrides` deletes it. |
| ugs-styles binding | Published by the `ugs-styles` repo, read-only here. A live binding is reported for someone to remove there. |
| Parquet superseded before the drop | Still referenced by an unexpired snapshot. The nightly `maintain` run collects it. |

There is no tombstone. The catalog is derived from the objects, so absence from GCS is absence
from the catalog.

## Not this tool

`maintain --drop-table` is the opposite case: a table whose parquet is *already* gone, which it
verifies and refuses to touch otherwise. It cannot retire a live topic.
