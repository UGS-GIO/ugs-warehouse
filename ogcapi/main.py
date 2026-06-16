"""Serverless OGC API - Features over the warehouse GeoParquet — DuckDB + FastAPI.

Cloud-native + scale-to-zero: there is NO database. Collections are derived from the live STAC
catalog (derive-from-truth), and feature queries run DuckDB over the GeoParquet read *straight off
the public CDN URL* (httpfs) — so the service holds no state and needs no GCP credentials. One
Cloud Run instance serves every layer; a request routes to the right collection's parquet. Bbox
filtering is pushed into DuckDB (ST_Intersects), not scanned in Python.

Endpoints (OGC API - Features Core):
  /                                landing page
  /conformance                     conformance classes
  /collections                     collection list (from STAC)
  /collections/{id}                collection metadata
  /collections/{id}/items          features (bbox, limit, offset) -> GeoJSON
  /collections/{id}/items/{fid}    a single feature

Env:
  STAC_CATALOG   catalog.json URL (default: prod)
  STAC_SKIP      comma collections to ignore (default ugs-publications — COG plates, no parquet)
  GEOM_COLUMN    geometry column in the parquet (default: geom)
"""
from __future__ import annotations

import json
import os
import urllib.request
from urllib.parse import urljoin

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request

CATALOG = os.environ.get("STAC_CATALOG", "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json")
SKIP = set(filter(None, os.environ.get("STAC_SKIP", "ugs-publications").split(",")))
GEOM = os.environ.get("GEOM_COLUMN", "geom")

CONFORMS = [
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
]

_collections: dict[str, dict] = {}   # id -> {title, item_href, parquet?, bbox?}
_con: duckdb.DuckDBPyConnection | None = None


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 (trusted https CDN)
        return json.loads(r.read().decode())


def _load_collections() -> None:
    """List collections + item ids from the STAC catalog. Parquet hrefs resolve lazily (per
    collection, on first query) to keep startup to ~2 fetches → fast cold start."""
    cat = _get_json(CATALOG)
    for cl in (l for l in cat.get("links", []) if l.get("rel") == "child"):
        curl = urljoin(CATALOG, cl["href"])
        cid_coll = curl.rstrip("/").split("/")[-2]
        if cid_coll in SKIP:
            continue
        coll = _get_json(curl)
        for il in (l for l in coll.get("links", []) if l.get("rel") == "item"):
            iurl = urljoin(curl, il["href"])
            cid = iurl.rstrip("/").split("/")[-2]  # …/<id>/<id>.json → <id>
            _collections[cid] = {"title": cid, "item_href": iurl}


def _resolve(cid: str) -> dict:
    """Lazily fetch the item JSON for `cid` to get its GeoParquet asset href + bbox; cache it."""
    c = _collections.get(cid)
    if c is None:
        raise HTTPException(404, f"collection '{cid}' not found")
    if "parquet" not in c:
        item = _get_json(c["item_href"])
        pq = next((a for a in item.get("assets", {}).values()
                   if "parquet" in (a.get("type") or "") or (a.get("href") or "").endswith(".parquet")), None)
        if not pq:
            raise HTTPException(404, f"collection '{cid}' has no GeoParquet asset")
        c["parquet"] = pq["href"]
        c["bbox"] = item.get("bbox")
        c["title"] = item.get("properties", {}).get("title", cid)
    return c


def _db() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        _con = duckdb.connect()
        _con.execute("LOAD spatial; LOAD httpfs;")  # baked into the image (no runtime download)
    return _con


app = FastAPI(title="UGS Warehouse — OGC API Features", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    try:
        _load_collections()
        print(f"[ogcapi] {len(_collections)} collections from {CATALOG}")
    except Exception as e:  # noqa: BLE001 — empty catalog beats a crash loop
        print(f"[ogcapi] collection load failed: {e}")


def _base(req: Request) -> str:
    return str(req.base_url).rstrip("/")


@app.get("/")
def landing(req: Request) -> dict:
    b = _base(req)
    return {
        "title": "UGS Warehouse — OGC API Features",
        "description": "Serverless OGC API Features over warehouse GeoParquet (DuckDB, no database).",
        "links": [
            {"rel": "self", "type": "application/json", "href": f"{b}/"},
            {"rel": "conformance", "type": "application/json", "href": f"{b}/conformance"},
            {"rel": "data", "type": "application/json", "href": f"{b}/collections"},
            {"rel": "service-desc", "type": "application/vnd.oai.openapi+json;version=3.0", "href": f"{b}/openapi.json"},
        ],
    }


@app.get("/conformance")
def conformance() -> dict:
    return {"conformsTo": CONFORMS}


def _collection_doc(req: Request, cid: str) -> dict:
    b = _base(req)
    c = _collections[cid]
    bbox = c.get("bbox")
    return {
        "id": cid,
        "title": c.get("title", cid),
        "extent": {"spatial": {"bbox": [bbox]}} if bbox else {},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "links": [
            {"rel": "self", "type": "application/json", "href": f"{b}/collections/{cid}"},
            {"rel": "items", "type": "application/geo+json", "href": f"{b}/collections/{cid}/items"},
        ],
    }


@app.get("/collections")
def collections(req: Request) -> dict:
    b = _base(req)
    return {
        "links": [{"rel": "self", "type": "application/json", "href": f"{b}/collections"}],
        "collections": [_collection_doc(req, cid) for cid in sorted(_collections)],
    }


@app.get("/collections/{cid}")
def collection(cid: str, req: Request) -> dict:
    if cid not in _collections:
        raise HTTPException(404, f"collection '{cid}' not found")
    return _collection_doc(req, cid)


def _cols(con: duckdb.DuckDBPyConnection, c: dict, src: str) -> list[str]:
    """Property columns (everything but the geometry), cached per collection."""
    if "cols" not in c:
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()
        c["cols"] = [r[0] for r in desc if r[0] != GEOM]
    return c["cols"]


def _features(cid: str, where: str = "", limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """Run DuckDB over the collection's GeoParquet (read off the CDN). Returns (features, matched).

    bbox/fid filters arrive in `where`; pushed into DuckDB, not scanned in Python. Properties are
    every column except the geometry, packed to JSON; geometry via ST_AsGeoJSON; the feature id is
    the row ordinal (parquet is hilbert-sorted, so the order is stable).
    """
    c = _resolve(cid)
    src = c["parquet"].replace("'", "''")
    con = _db()
    cols = _cols(con, c, src)
    matched = con.execute(f"SELECT count(*) FROM read_parquet('{src}'){where}").fetchone()[0]
    page = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
    struct = ", ".join(f'"{col}" := "{col}"' for col in cols) or "'_' := NULL"
    q = (f"SELECT row_number() OVER () - 1 AS _fid, "
         f"ST_AsGeoJSON({GEOM}) AS _g, "
         f"to_json(struct_pack({struct})) AS _p "
         f"FROM read_parquet('{src}'){where}{page}")
    feats = []
    for fid, g, p in con.execute(q).fetchall():
        feats.append({
            "type": "Feature", "id": int(fid),
            "geometry": json.loads(g) if g else None,
            "properties": json.loads(p) if p else {},
        })
    return feats, int(matched)


@app.get("/collections/{cid}/items")
def items(cid: str, req: Request,
          bbox: str | None = Query(None, description="minx,miny,maxx,maxy (CRS84)"),
          limit: int = Query(100, ge=1, le=10000), offset: int = Query(0, ge=0)) -> dict:
    where = ""
    if bbox:
        try:
            w, s, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minx,miny,maxx,maxy")
        where = f" WHERE ST_Intersects({GEOM}, ST_MakeEnvelope({w},{s},{e},{n}))"
    feats, matched = _features(cid, where, limit, offset)
    b = _base(req)
    return {
        "type": "FeatureCollection",
        "features": feats,
        "numberReturned": len(feats),
        "numberMatched": matched,
        "links": [{"rel": "self", "type": "application/geo+json",
                   "href": f"{b}/collections/{cid}/items"}],
    }


@app.get("/collections/{cid}/items/{fid}")
def item(cid: str, fid: int, req: Request) -> dict:
    # _fid is the row ordinal; fetch that single row (parquet is hilbert-sorted = stable order).
    feats, _ = _features(cid, f" QUALIFY row_number() OVER () - 1 = {int(fid)}")
    if not feats:
        raise HTTPException(404, f"feature {fid} not found in '{cid}'")
    return feats[0]
