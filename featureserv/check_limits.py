"""Build-time gate: the response cap and the container's memory are one decision.

featureserv buffers the entire FeatureCollection before writing it, so the largest response a
client can ask for has to fit in the container. `LimitMax` lives in duckdb_featureserv.toml and
`--memory` lives in cloudbuild.yaml, and nothing otherwise stops the two drifting apart. When they
do, the failure is not a clean error: Cloud Run SIGKILLs the container mid-response ("terminated on
signal 9") and returns a 503, which also takes out any request that happened to be in flight on the
same instance. That is how ALL-5410 happened -- LimitMax was raised to 100000 on the reasoning that
it "clears the largest topic", with no reference to the 512Mi the service was actually running on.

This refuses to build a (memory, LimitMax) pair that nobody has measured.

Scope caveat: every VALIDATED pair below was measured under SEQUENTIAL load (single full pulls and
unpaced paging). This gate does NOT model concurrent requests, and passing it is NOT evidence a
(memory, LimitMax) pair survives parallel client load. The concurrent-load failure mode (ALL-5869 --
one in-process DuckDB plus Go whole-response buffering, shared across in-flight requests) is bounded
elsewhere: Cloud Run --concurrency and the DuckDB memory_limit pragma in featureserv, neither of which
this file sees. Do not read a monotonicity pass here as "safe under concurrency."

It deliberately does NOT try to predict memory from feature counts. The measurements we have do not
fit one multiplier: a single full 84,756-feature pull is a 122MB response that 2Gi serves happily
and 1Gi cannot, while *paged* 20k requests -- ~29MB each -- OOM at 512Mi despite being far smaller,
because consecutive requests outrun GC. A model fitted to that would be wrong in one direction or
the other, and a wrong model wired into a build gate trades a real failure for false confidence.
So: measure, record the pair here, and require a recorded pair.

Monotonicity is the one inference allowed: more memory with the same cap is safe, and a smaller cap
with the same memory is safe. Anything outside that has to be measured and added.

Run at image build (see Dockerfile). Env/args: --memory (e.g. 2Gi), --config, --db.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib

import duckdb

# (memory_mb, limit_max) pairs that were actually exercised against the real catalog, with what was
# run. Add a row only after measuring -- the point of this file is that the list means something.
VALIDATED: dict[tuple[int, int], str] = {
    (2048, 100000): (
        "repeated full-layer pulls of enmin_plss_sections (84,756 features / 122MB): "
        "11.5s cold then ~3s warm, no OOM; and 3 rounds of unpaced 5x20k paging"
    ),
    (1024, 20000): (
        "unpaced paging, 5x20k over enmin_plss_sections, full 84,756 traversed, no OOM. "
        "NOTE 1Gi does NOT survive a single full 100000 pull -- that is why the cap matters here"
    ),
}

# Pairs measured to fail, kept so the error message can say "known bad" rather than "unmeasured".
KNOWN_BAD: dict[tuple[int, int], str] = {
    (512, 100000): "OOM on a single full pull (ALL-5410, and what was live in prod)",
    (1024, 100000): "OOM on a single full pull",
    (512, 20000): "OOM on page 2-4 of an unpaced paging walk",
}


def parse_memory_mb(value: str) -> int:
    """'2Gi' / '512Mi' / '2G' / '2048' -> MiB. Cloud Run accepts the Ki/Mi/Gi suffixes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMG]i?)?\s*", value or "", re.IGNORECASE)
    if not m:
        raise SystemExit(f"[check_limits] cannot parse --memory={value!r}")
    n = float(m.group(1))
    unit = (m.group(2) or "Mi").lower().rstrip("i")
    return int(n * {"k": 1 / 1024, "m": 1, "g": 1024}[unit])


def limit_max(config_path: str) -> int:
    with open(config_path, "rb") as fh:
        cfg = tomllib.load(fh)
    try:
        return int(cfg["Paging"]["LimitMax"])
    except KeyError as e:
        raise SystemExit(f"[check_limits] {config_path} has no Paging.LimitMax ({e})") from e


def largest_collection(db_path: str) -> tuple[str, int] | None:
    """(collection, rows) for the biggest collection in the built database.

    Row counts come from the parquet footers rather than a scan, so this is metadata-cheap. Purely
    informational: it says whether the cap can still return any topic whole, which is the number
    that moves as the catalog grows.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        names = [r[0] for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()]
        biggest = None
        for name in names:
            try:
                n = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            except Exception as e:  # one unreadable layer must not fail the build here
                print(f"  ! {name}: {e}", file=sys.stderr)
                continue
            if biggest is None or n > biggest[1]:
                biggest = (name, n)
        return biggest
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory", required=True, help="the container's memory limit, e.g. 2Gi")
    ap.add_argument("--config", default="duckdb_featureserv.toml")
    ap.add_argument("--db", default=None, help="built database, for the informational row counts")
    args = ap.parse_args()

    mem = parse_memory_mb(args.memory)
    cap = limit_max(args.config)

    covered = [(vm, vl) for (vm, vl) in VALIDATED if mem >= vm and cap <= vl]
    if covered:
        vm, vl = max(covered)
        print(f"[check_limits] OK  memory={mem}MiB LimitMax={cap} "
              f"-- covered by measured pair ({vm}MiB, {vl}): {VALIDATED[(vm, vl)]}")
    else:
        bad = [(bm, bl) for (bm, bl) in KNOWN_BAD if mem <= bm and cap >= bl]
        why = (f"this is a KNOWN BAD pair -- {KNOWN_BAD[max(bad)]}" if bad
               else "this pair has not been measured")
        raise SystemExit(
            f"[check_limits] REFUSING TO BUILD\n"
            f"  memory   = {args.memory} ({mem}MiB), from cloudbuild.yaml _FEATURES_MEMORY\n"
            f"  LimitMax = {cap}, from {args.config}\n"
            f"  {why}.\n"
            f"  Measured-safe pairs (memory MiB, LimitMax): "
            f"{sorted(VALIDATED)}\n"
            f"  featureserv buffers the whole response, so an unfitting pair does not degrade -- it\n"
            f"  OOM-kills the container and 503s, taking concurrent requests with it.\n"
            f"  Either pick a covered pair, or measure this one and add it to VALIDATED in\n"
            f"  featureserv/check_limits.py. To measure:\n"
            f"    docker run -d --name t --memory={args.memory} --memory-swap={args.memory} \\\n"
            f"      -p 9000:9000 <image>\n"
            f"    # single full pull, then an unpaced paging walk -- both must stay up:\n"
            f"    curl -s -o /dev/null -w '%{{http_code}}\\n' \\\n"
            f"      localhost:9000/collections/<largest>/items?limit={cap}\n"
            f"    docker inspect t --format '{{{{.State.OOMKilled}}}}'   # must be false"
        )

    if args.db:
        biggest = largest_collection(args.db)
        if biggest:
            name, rows = biggest
            if rows > cap:
                print(f"[check_limits] note: {name} has {rows:,} features, above LimitMax={cap:,} "
                      f"-- clients must page it (numberMatched + next links make that visible)")
            else:
                print(f"[check_limits] largest collection {name} = {rows:,} features, "
                      f"within LimitMax={cap:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
