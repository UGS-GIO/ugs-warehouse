"""Build the DuckDB database that duckdb_featureserv serves — one collection per warehouse
serving-topic, sourced from its GeoParquet on the CDN. The collection list derives from the live
STAC catalog (derive-from-truth).

MODE=table (default): materialize each layer into the db — a build-time snapshot. Guaranteed to
work (featureserv discovers real tables; no runtime httpfs needed). Refreshes on each image build.
MODE=view: CREATE VIEW over read_parquet(<cdn url>) — live data, lake-native, but only works if
featureserv loads httpfs at query time (verify before switching).

Run at image build (see Dockerfile). Env: STAC_CATALOG, STAC_SKIP, DB_PATH, MODE, GEOM_COLUMN.
"""
from __future__ import annotations

import json
import os
import urllib.request
from urllib.parse import urljoin

import duckdb

CATALOG = os.environ.get("STAC_CATALOG", "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json")
SKIP = set(filter(None, os.environ.get("STAC_SKIP", "ugs-publications").split(",")))
DB_PATH = os.environ.get("DB_PATH", "database.duckdb")
MODE = os.environ.get("MODE", "view")  # view (live, tiny — confirmed: featureserv loads httpfs) | table (snapshot)


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (trusted https CDN)
        return json.loads(r.read().decode())


def main() -> int:
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    cat = _get(CATALOG)
    n = 0
    for cl in (l for l in cat.get("links", []) if l.get("rel") == "child"):
        curl = urljoin(CATALOG, cl["href"])
        if curl.rstrip("/").split("/")[-2] in SKIP:
            continue
        coll = _get(curl)
        for il in (l for l in coll.get("links", []) if l.get("rel") == "item"):
            iurl = urljoin(curl, il["href"])
            cid = iurl.rstrip("/").split("/")[-2]  # …/<id>/<id>.json → <id>
            item = _get(iurl)
            pq = next((a["href"] for a in item.get("assets", {}).values()
                       if "parquet" in (a.get("type") or "") or (a.get("href") or "").endswith(".parquet")), None)
            if not pq:
                continue
            src = pq.replace("'", "''")
            kw = "TABLE" if MODE == "table" else "VIEW"
            con.execute(f'CREATE OR REPLACE {kw} "{cid}" AS SELECT * FROM read_parquet(\'{src}\')')
            n += 1
            print(f"  {kw} {cid}")
    con.close()
    print(f"[gen_db] {n} collections -> {DB_PATH} (mode={MODE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
