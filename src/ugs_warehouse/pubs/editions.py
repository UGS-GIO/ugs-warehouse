"""Quad-based edition graph for the geologic-map corpus.

Links each map's `series_id` to its predecessor/successor by (quad, tier) so an older map gets
`deprecated` + a `successor-version` link, and its replacement gets a `predecessor-version` link
back — the STAC Versioning Indicators fields `core.stac.build_item` / `sink_stac.build_item`
already stamp when handed an `edition` dict (Task 2).

Authoritative quad<->series source (SME-confirmed — supersedes the original AGOL-confirmation
gate; ALL-5911 Task 4): `quad_name` comes from the staged footprints parquet (`quad_by_series()`,
keyed by `series_id`) — the pub feed itself (`source.read_pubs()`) has no `quad_name` column in
prod, which made edition detection silently produce nothing. A pub's own `p["quad_name"]` (when
present, e.g. in tests or a future feed) is kept only as a fallback for a pub absent from
footprints. `pub_year`/`pub_scale` are still read straight off each pub dict.

Grouping key = (normalized `quad_name`, tier). Editions must share BOTH the quad and the tier:
the same quad name in another tier (a 30' x 60' "Tooele Quad" vs the 1 x 2 degree Tooele sheet) is a
different map series, not an edition of this one. The tier is the map's mosaic tier (its portal
layer, `scale.mosaic_tier_of`) when the caller passes `tier_by_sid`, so an edition never supersedes a
map in a different mosaic; a map with no tiered layer falls back to its scale band (`scale.tier_of`),
kept in a separate key space so a band never matches a mosaic tier.

`DM`/`DR` suffixes (`M-206` vs `M-206DM`) are genuinely distinct publications that happen to land
in the same (quad, tier) group — never collapsed into one series_id.

Conservative by design (never mislabel a published record):
  - a blank `quad_name` -> not a quad map at all (true of most pubs) -> skipped, no warning.
  - a quad map with no mosaic tier whose `pub_scale` `tier_of` can't parse -> excluded, warned.
  - within a (quad, tier) group, a `pub_year` that's missing or ties with a sibling's makes that
    entry's position ambiguous -> excluded from the graph and warned for human review, while any
    cleanly-ordered siblings in the same group still link normally.
An entry absent from the returned graph carries no edition information at all: `ingest.py` looks
it up with `graph.get(series_id)`, gets `None`, and passes `edition=None` — `sink_stac.build_item`
then stamps no `version`/`deprecated`/predecessor/successor/latest for it.

Import-cheap on purpose: this module (and `geolmap_mosaics`, and `sink_stac`, imported lazily
below) keeps every rasterio/geopandas/pyogrio/shapely/pygltflib import inside function bodies,
not at module load — verified by reading their import chains — so `editions.py` and its tests
run with only the base + `[dev]` extras, no `[pubs]` raster stack required.
"""
from __future__ import annotations

from collections import defaultdict

from ..core import config, stac
from .scale import mosaic_tier_of, tier_of


def _item_href(p: dict) -> str:
    """Public STAC item URL for an edition member. Resolved through its REAL top-level collection
    group (`sink_stac.collection_group`) — a quad map isn't always in `ugs-publications`: MD-series
    pubs route to `ugs-mining-district-files` and foreign-publisher pubs (BYU/AAPG/USU/UU, …) route
    to `ugs-external`, independent of edition grouping (which only looks at quad+tier). Hardcoding
    `ugs-publications` here would build a dead 404 href for those — this mirrors how
    `sink_stac.build_item` builds its own collection_path (group + series_code), so the two
    can never disagree."""
    from .sink_stac import collection_group, item_id_for, series_code  # lazy: raster-dep-free

    sid = (p.get("series_id") or "").strip()
    collection_path = f"{collection_group(p)}/{series_code(sid)}"
    return config.public_url(stac.item_object_path(collection_path, item_id_for(sid)))


def _year_key(raw: str | None) -> str | None:
    """A `pub_year` counts as orderable only when it's exactly a 4-digit year. Real vendored data
    has values like "1997 (rev 2017)" — genuinely ambiguous (which year would we sort on?) — so
    that is treated the same as blank: left out, warned, never guessed at."""
    y = (raw or "").strip()
    return y if len(y) == 4 and y.isdigit() else None


def _link_group(members: list[dict], out: dict[str, dict], *, quad: str, tier: str) -> None:
    year_counts: dict[str, int] = defaultdict(int)
    for p in members:
        y = _year_key(p.get("pub_year"))
        if y is not None:
            year_counts[y] += 1

    ordered: list[tuple[str, dict]] = []
    for p in members:
        sid = (p.get("series_id") or "").strip()
        y = _year_key(p.get("pub_year"))
        if y is None:
            print(f"[editions] WARNING: {sid} (quad={quad!r}, scale={tier}) has a missing/"
                  f"unparseable pub_year ({p.get('pub_year')!r}) — left out of the edition graph "
                  f"for human review")
            continue
        if year_counts[y] > 1:
            print(f"[editions] WARNING: {sid} (quad={quad!r}, scale={tier}) pub_year {y!r} ties "
                  f"with another map in the same group — left out of the edition graph for "
                  f"human review")
            continue
        ordered.append((y, p))

    ordered.sort(key=lambda t: t[0])
    n = len(ordered)
    latest_p = ordered[-1][1] if ordered else None
    for i, (yr, p) in enumerate(ordered):
        sid = (p.get("series_id") or "").strip()
        older = ordered[i - 1][1] if i > 0 else None
        newer = ordered[i + 1][1] if i + 1 < n else None
        is_latest = p is latest_p
        out[sid] = {
            "version": yr,
            "deprecated": newer is not None,
            "predecessor_href": _item_href(older) if older else None,
            "successor_href": _item_href(newer) if newer else None,
            "latest_href": None if is_latest else _item_href(latest_p),
        }


def footprint_rows() -> list[tuple[str, str, str, str]]:
    """(UPPER series_id, quad_name, geomaps_service, servName) for every row of the staged footprints
    parquet (built by pubs.footprints). Read via gcs.get_bytes (obstore/ADC, the repo's GCS IO path,
    no httpfs) + a local duckdb read, the same way footprints.py reads it. Fail loud if the parquet is
    absent: without it neither edition detection nor mosaic tiering can work."""
    import os
    import tempfile

    import duckdb  # base dep; kept function-level to preserve editions.py's import-cheapness

    from ..core import gcs
    from . import identity  # cheap (os/dataclasses/urllib only) — avoids footprints' `requests` dep

    obj = f"{identity.FOOTPRINTS_PREFIX}/footprints.parquet"  # == footprints.PARQUET_OBJECT
    try:
        data = gcs.get_bytes(obj)  # obstore; raises if absent
    except Exception as e:  # noqa: BLE001 — surface it, don't silently degrade
        raise RuntimeError(
            f"[editions] staged footprints parquet gs://.../{obj} unreadable "
            f"({e}); run `python -m ugs_warehouse.pubs.footprints` first") from e

    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        with duckdb.connect() as con:
            try:
                rows = con.execute(
                    "SELECT upper(trim(series_id)), coalesce(quad_name, ''), "
                    "coalesce(trim(geomaps_service), ''), coalesce(trim(servName), '') "
                    "FROM read_parquet(?) WHERE coalesce(trim(series_id), '') <> ''",
                    [tmp.name]).fetchall()
            except duckdb.Error as e:  # e.g. a parquet staged before geomaps_service existed
                raise RuntimeError(f"[editions] footprints parquet gs://.../{obj} unusable: {e}") from e
    finally:
        os.unlink(tmp.name)
    return [(str(s), str(q), str(g), str(n)) for s, q, g, n in rows]


def quad_by_series(rows: list[tuple[str, str, str, str]] | None = None) -> dict[str, str]:
    """{UPPER series_id -> quad_name} from the footprints (see `footprint_rows`)."""
    return {s: q for s, q, _, _ in (rows if rows is not None else footprint_rows()) if q}


def layers_by_series(rows: list[tuple[str, str, str, str]] | None = None
                     ) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """{UPPER series_id -> (geomaps_service values, servName values)} across that map's footprint
    rows: the geologic map portal layer(s) a map belongs to. A map can have several footprint rows."""
    svc: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}
    for s, _, g, n in (rows if rows is not None else footprint_rows()):
        if g:
            svc.setdefault(s, set()).add(g)
        if n:
            names.setdefault(s, set()).add(n)
    return {s: (frozenset(svc.get(s, ())), frozenset(names.get(s, ()))) for s in svc.keys() | names.keys()}


def mosaic_tier_by_series(layers: dict[str, tuple[frozenset[str], frozenset[str]]]) -> dict[str, str]:
    """{UPPER series_id -> mosaic tier} for every map in exactly one tiered portal layer. The mosaics
    bake and the pub catalog both use this, so a map's edition group and its mosaic link agree."""
    return {s: t for s, v in layers.items() if (t := mosaic_tier_of(*v))}


def edition_graph(pubs: list[dict], quad_by_sid: dict[str, str] | None = None,
                  tier_by_sid: dict[str, str] | None = None) -> dict[str, dict]:
    """{series_id -> edition dict} for every quad map placed unambiguously in a (quad, tier)
    group — `{"version", "deprecated", "predecessor_href", "successor_href", "latest_href"}`, the
    shape `sink_stac.build_item(..., edition=...)` consumes. A pub not covered by any rule below is
    simply absent from the result (see the module docstring).

    `quad_by_sid` (`{UPPER series_id: quad_name}`) is the footprints-sourced quad map; defaults to
    `quad_by_series()` (a live parquet read) when omitted. Pass `{}` to force the feed-only
    fallback with no network call — see `tests/test_pubs_editions.py`. `tier_by_sid`
    (`{UPPER series_id: mosaic tier}`, from `mosaic_tier_by_series`) groups each map by its mosaic
    tier instead of its scale band; production callers pass it, tests may omit it."""
    if quad_by_sid is None:
        quad_by_sid = quad_by_series()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        quad = (quad_by_sid.get(sid.upper()) or (p.get("quad_name") or "")).strip()
        if not sid or not quad:
            continue  # no quad_name -> not a quad map (the common case, not an anomaly)
        mosaic = (tier_by_sid or {}).get(sid.upper())
        band = None if mosaic else tier_of(p.get("pub_scale"))
        tier = f"mosaic {mosaic}" if mosaic else (f"band {band}" if band else None)
        if tier is None:
            print(f"[editions] WARNING: {sid} (quad={quad!r}) has a missing/unparseable "
                  f"pub_scale ({p.get('pub_scale')!r}) — excluded from edition detection")
            continue
        norm_quad = " ".join(quad.split()).casefold()
        groups[(norm_quad, tier)].append(p)

    out: dict[str, dict] = {}
    for (_norm_quad, tier), members in groups.items():
        first_sid = (members[0].get("series_id") or "").strip()
        quad_label = (quad_by_sid.get(first_sid.upper())
                      or (members[0].get("quad_name") or "")).strip()
        _link_group(members, out, quad=quad_label, tier=tier)
    return out
