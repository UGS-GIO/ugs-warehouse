"""Build the DuckDB database that duckdb_featureserv serves — one collection per warehouse
serving-topic, sourced from its GeoParquet on the CDN. The collection list derives from the live
STAC catalog (derive-from-truth).

Runs twice: once at image build (a cold-floor snapshot baked into the image) and again in the
container entrypoint on every cold start, so a topic ingested standalone becomes queryable without
waiting for an unrelated push to rebuild the image (#89). Startup must never hang on the catalog,
so the boot run is bounded by --deadline and leaves the baked snapshot in place on failure; the
build run is --strict.

MODE=view (default): CREATE VIEW over read_parquet(<cdn url>) — live data, lake-native (confirmed:
featureserv loads httpfs at query time). MODE=table materializes each layer into the db instead —
a snapshot, fallback if a future featureserv drops view support.

Env: STAC_CATALOG, STAC_SKIP, DB_PATH, MODE, GEN_DB_{HTTP_TIMEOUT,DEADLINE,WORKERS}.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urljoin

import duckdb

CATALOG = os.environ.get("STAC_CATALOG", "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json")
SKIP = set(filter(None, os.environ.get("STAC_SKIP", "ugs-publications").split(",")))
DB_PATH = os.environ.get("DB_PATH", "database.duckdb")
MODE = os.environ.get("MODE", "view")  # view (live, tiny — confirmed: featureserv loads httpfs) | table (snapshot)
HTTP_TIMEOUT = float(os.environ.get("GEN_DB_HTTP_TIMEOUT", "10"))
DEADLINE = float(os.environ.get("GEN_DB_DEADLINE", "60"))
WORKERS = int(os.environ.get("GEN_DB_WORKERS", "8"))
# Collection ids are interpolated into DDL below. Mirrors core/identifiers.py — this image
# installs only this file, not the package.
_IDENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


def warn(msg: str) -> None:
    """Degradation and failure notices go to stderr. Cloud Run maps stderr to ERROR severity, so a
    catalog that has been unreachable for a week is alertable instead of reading as a healthy boot.
    Progress lines stay on stdout."""
    print(msg, file=sys.stderr)


def _remaining(deadline: float | None) -> float:
    """Seconds of budget left, floored just above zero so a socket timeout of 0 (= no timeout)
    can never be passed to urlopen."""
    if deadline is None:
        return HTTP_TIMEOUT
    return max(0.05, min(HTTP_TIMEOUT, deadline - time.monotonic()))


def _get(url: str, deadline: float | None = None) -> dict:
    # Bound each call by whichever is nearer: its own timeout or what is left of the run budget.
    # Without the deadline term a single slow child can spend the whole budget on retries the
    # caller has no way to interrupt.
    with urllib.request.urlopen(url, timeout=_remaining(deadline)) as r:  # noqa: S310 (trusted https CDN)
        return json.loads(r.read().decode())


def _parquet_href(item: dict) -> str | None:
    for a in (item.get("assets") or {}).values():
        href = a.get("href") or ""
        if "parquet" in (a.get("type") or "") or href.endswith(".parquet"):
            return href
    return None


def _collection_layers(curl: str, coll: dict, deadline: float | None = None) -> list[tuple[str, str]]:
    """[(item id, parquet url)] for one flat collection.

    Reads the `items` index — one GET for the whole collection, and it already carries each item's
    assets — instead of the `item` links, which cost a GET each (~34 catalog-wide round trips vs
    ~9 at current size). Falls back to the per-item docs if the index is missing or unreadable.
    """
    idx = next((lnk for lnk in coll.get("links", []) if lnk.get("rel") == "items"), None)
    if idx:
        try:
            doc = _get(urljoin(curl, idx["href"]), deadline)
            return [(it["id"], pq) for it in doc.get("items", []) if (pq := _parquet_href(it))]
        except Exception as e:
            warn(f"  ! items index unusable ({e}) — falling back to per-item docs")
    layers: list[tuple[str, str]] = []
    for lnk in (x for x in coll.get("links", []) if x.get("rel") == "item"):
        # The fallback is one GET per item, so a collection with many items is where a slow CDN
        # would otherwise burn the entire budget inside a single worker -- give up on the rest of
        # this collection rather than the whole run.
        if deadline is not None and time.monotonic() > deadline:
            warn(f"  ! deadline hit walking {curl} — {len(layers)} item(s) resolved")
            break
        iurl = urljoin(curl, lnk["href"])
        try:
            item = _get(iurl, deadline)
        except Exception as e:
            warn(f"  ! skip {iurl}: {e}")
            continue
        if pq := _parquet_href(item):
            layers.append((item.get("id") or iurl.rstrip("/").split("/")[-2], pq))
    return layers


def _child_layers(curl: str, deadline: float | None = None) -> list[tuple[str, str]]:
    """Layers under one root child — a collection, or a Catalog that publishes a rollup items index.

    `ugs-serving-topics` is a Catalog of per-schema collections but carries a `rel:items` link to an
    index spanning all of them, so it resolves in one fetch here exactly like a flat collection. A
    Catalog without that link (ugs-external, ugs-mining-district-files, ugs-rasters) has no item
    links and contributes nothing — featureserv serves the vector layers only."""
    try:
        coll = _get(curl, deadline)
    except Exception as e:
        warn(f"  ! skip collection {curl}: {e}")
        return []
    return _collection_layers(curl, coll, deadline)


def discover(deadline: float) -> list[tuple[str, str]]:
    """Every (collection id, parquet url) in the catalog. Children are fetched concurrently — it's
    all network, and at boot this runs inside the container's startup budget.

    The budget is enforced with `wait(timeout=...)` rather than a clock check around a blocking
    `.result()`. Two reasons the obvious version does not hold: the check only gets a turn between
    futures, so it cannot fire while a slow child is in flight; and leaving a `with
    ThreadPoolExecutor` block calls `shutdown(wait=True)`, so even a raised TimeoutError waits for
    every in-flight future to drain before control returns. Measured overshoot on that shape was 8s
    against a 1s budget. Since nothing listens on :9000 until this returns, an overrun is not a
    stale collection list -- it is a container that never becomes ready.
    """
    cat = _get(CATALOG, deadline)
    urls = [urljoin(CATALOG, lnk["href"]) for lnk in cat.get("links", []) if lnk.get("rel") == "child"]
    urls = [u for u in urls if u.rstrip("/").split("/")[-2] not in SKIP]
    layers: list[tuple[str, str]] = []
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        futs = [pool.submit(_child_layers, u, deadline) for u in urls]
        done, pending = wait(futs, timeout=max(0.0, deadline - time.monotonic()))
        for f in pending:
            f.cancel()
        if pending:
            raise TimeoutError(
                f"catalog scan exceeded {DEADLINE:.0f}s ({len(pending)} of {len(futs)} children unresolved)")
        for f in futs:  # iterate futs, not done, so the layer order stays deterministic
            layers.extend(f.result())
    finally:
        # wait=False so a worker still blocked on a socket cannot hold up the shutdown; the
        # per-call timeouts in _get bound how long those threads can linger.
        pool.shutdown(wait=False, cancel_futures=True)
    return layers


def _bound_layers(db: str) -> dict[str, str] | None:
    """{collection: parquet url} already bound in the db at `db`, or None if there isn't one to
    read. Derived from the db's own view definitions — no sidecar bookkeeping to drift out of sync
    with what featureserv will actually serve."""
    if MODE == "table" or not os.path.exists(db):
        return None  # a materialized table holds data, not a pointer: "same url" ≠ "current"
    try:
        con = duckdb.connect(db, read_only=True)
        try:
            rows = con.execute("SELECT view_name, sql FROM duckdb_views() WHERE NOT internal").fetchall()
        finally:
            con.close()
    except Exception as e:
        warn(f"  ! existing db unreadable ({e}) — rebinding everything")
        return None
    # The stored SQL is what we wrote, so this pattern is ours, not a general SQL parse.
    bound = {name: m.group(1) for name, sql in rows
             if (m := re.search(r"read_parquet\('([^']+)'\)", sql or ""))}
    return bound or None


def apply(path: str, layers: list[tuple[str, str]], prior: dict[str, str] | None,
          deadline: float) -> tuple[int, int]:
    """Bring the db at `path` in line with `layers`. Returns (collections bound, left unbound).

    Binding a view costs a remote parquet footer read (~0.3s each), which is essentially the whole
    cost of a run — so when `prior` tells us a layer is already bound to the same parquet, leave it
    alone. On an unchanged catalog that makes this a no-op and boot costs only the catalog scan.

    This phase, not the catalog scan, is what a slow CDN stretches, so `deadline` bounds it too:
    nothing listens on :9000 until we return, and Cloud Run kills a revision that starts too slowly.
    """
    kw = "TABLE" if MODE == "table" else "VIEW"
    desired = dict(layers)
    # A materialized table holds data, not a pointer, so "unchanged url" doesn't mean "current".
    add = dict(desired) if (prior is None or kw == "TABLE") else {
        cid: pq for cid, pq in desired.items() if prior.get(cid) != pq
    }
    drop = [] if prior is None else [cid for cid in prior if cid not in desired]
    bound = {cid for cid, pq in desired.items() if cid not in add and (prior or {}).get(cid) == pq}
    con = duckdb.connect(path)
    try:
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        for cid in drop:
            if not _IDENT_RE.fullmatch(cid):
                warn(f"  ! skip drop {cid!r}: not a bare identifier")
                continue
            con.execute(f'DROP {kw} IF EXISTS "{cid}"')
            print(f"  - {kw} {cid}")
        pending = list(add.items())
        while pending:
            if time.monotonic() > deadline:
                warn(f"  ! deadline hit — {len(pending)} layer(s) left unbound")
                break
            cid, pq = pending.pop(0)
            if not _IDENT_RE.fullmatch(cid):
                warn(f"  ! skip {cid!r}: not a bare identifier")
                continue
            src = pq.replace("'", "''")
            # One unreadable layer must cost us that collection, not the whole catalog.
            try:
                con.execute(f'CREATE OR REPLACE {kw} "{cid}" AS SELECT * FROM read_parquet(\'{src}\')')
            except Exception as e:
                warn(f"  ! skip {cid}: {e}")
                continue
            bound.add(cid)
            print(f"  {kw} {cid}")
        return len(bound), len(pending)
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the featureserv collection database.")
    ap.add_argument("--out", default=DB_PATH, help="database to write (default $DB_PATH)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on failure (image build); default soft-fails (container start)")
    ap.add_argument("--deadline", type=float, default=DEADLINE, help="seconds allowed for the catalog scan")
    ap.add_argument("--rebuild", action="store_true", help="rebind every layer, ignoring the existing db")
    args = ap.parse_args()

    started = time.monotonic()
    deadline = started + args.deadline  # one wall clock for the scan and the binding both
    tmp = f"{args.out}.new"
    try:
        layers = discover(deadline)
        for stale in (tmp, f"{tmp}.wal"):  # a killed prior run can leave these behind
            if os.path.exists(stale):
                os.remove(stale)
        prior = None if args.rebuild else _bound_layers(args.out)
        if prior is not None and dict(layers) == prior:
            print(f"[gen_db] catalog unchanged — {len(prior)} collections, nothing to bind "
                  f"({time.monotonic() - started:.1f}s)")
            return 0
        if prior is not None:
            shutil.copyfile(args.out, tmp)  # mutate a copy; the live db stays servable until the swap
        n, unbound = apply(tmp, layers, prior, deadline)
        if not n:
            raise RuntimeError("catalog resolved no collections")
        # Partial is progress when we started from an existing db — the layers we did bind are
        # additive and a later start finishes the rest. Starting from nothing it isn't: swapping a
        # half-bound db over the baked one would *remove* collections that were serving fine.
        if unbound and prior is None:
            raise RuntimeError(f"deadline hit with {unbound} of {n + unbound} layers unbound")
    except Exception as e:
        warn(f"[gen_db] FAILED after {time.monotonic() - started:.1f}s: {e}")
        # Leave whatever is already at --out alone. At boot that's the image-baked snapshot: stale,
        # but it serves. A bad CDN moment degrades the collection list, it doesn't block startup.
        for leftover in (tmp, f"{tmp}.wal"):
            if os.path.exists(leftover):
                os.remove(leftover)
        return 1 if args.strict else 0
    os.replace(tmp, args.out)  # atomic — featureserv can never open a half-written db
    # DuckDB names the WAL after the database path, so a swap that moved only the db would orphan
    # tmp.wal and silently drop whatever it held. con.close() checkpoints, so there should never be
    # one -- move it with the db rather than trust that, and clear any stale WAL left by the file
    # we just replaced.
    if os.path.exists(f"{tmp}.wal"):
        os.replace(f"{tmp}.wal", f"{args.out}.wal")
    elif os.path.exists(f"{args.out}.wal"):
        os.remove(f"{args.out}.wal")
    os.chmod(args.out, 0o666)  # DuckDB opens it read-write
    print(f"[gen_db] {n} collections -> {args.out} (mode={MODE}, {time.monotonic() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
