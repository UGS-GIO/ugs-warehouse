#!/bin/sh
# The app spawns and supervises go-pmtiles itself (app.py `_lifespan`), because it has to be able
# to RESTART it: go-pmtiles caches each archive's directory forever over the HTTP backend, so a
# re-ingest is only picked up by a new process. Keeping the child under the app means one thing
# owns that decision, and if the child dies the app's next tile request restarts it.
set -eu
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}"
