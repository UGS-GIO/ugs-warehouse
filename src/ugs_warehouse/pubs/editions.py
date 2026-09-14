"""Quad-based edition graph for the geologic-map corpus.

Links each map's `series_id` to its predecessor/successor by (quad, scale) so an older map gets
`deprecated` + a `successor-version` link, and its replacement gets a `predecessor-version` link
back — the STAC Versioning Indicators fields `core.stac.build_item` / `sink_stac.build_item`
already stamp when handed an `edition` dict (Task 2).

Authoritative quad<->series source (SME-confirmed — supersedes the original AGOL-confirmation
gate; ALL-5911 Task 4): the pub record itself, as `source.read_pubs()` already returns it — no
live AGOL/network fetch. `series_id`, `quad_name`, `pub_year`, `pub_scale` are read straight off
each pub dict. The front-end's authoritative displayed `quad_name` IS the UGSpubs value, i.e. this
same pub-record field.

Grouping key = (normalized `quad_name`, scale tier). Editions must share BOTH the quad and the
scale tier — same quad at a different scale is a different map series, not an edition of this one.
Scale parsing is reused, not reinvented: `scale.tier_of` (the same 24k/250k/500k tiers
the mosaics producer bins COGs into) is the single source of truth for "what scale tier is this
publication". `DM`/`DR` suffixes (`M-206` vs `M-206DM`) are genuinely distinct publications that
happen to land in the same (quad, scale) group — never collapsed into one series_id.

Conservative by design (never mislabel a published record):
  - a blank `quad_name` -> not a quad map at all (true of most pubs) -> skipped, no warning.
  - a quad map whose `pub_scale` `tier_of` can't parse -> excluded from every group, warned.
  - within a (quad, scale) group, a `pub_year` that's missing or ties with a sibling's makes that
    entry's position ambiguous -> excluded from the graph and warned for human review, while any
    cleanly-ordered siblings in the same group still link normally.
An entry absent from the returned graph carries no edition information at all: `ingest.py` looks
it up with `graph.get(series_id)`, gets `None`, and passes `edition=None` — `sink_stac.build_item`
then stamps no `version`/`deprecated`/predecessor/successor for it.

Import-cheap on purpose: this module (and `geolmap_mosaics`, and `sink_stac`, imported lazily
below) keeps every rasterio/geopandas/pyogrio/shapely/pygltflib import inside function bodies,
not at module load — verified by reading their import chains — so `editions.py` and its tests
run with only the base + `[dev]` extras, no `[pubs]` raster stack required.
"""
from __future__ import annotations

from collections import defaultdict

from ..core import config, stac
from .scale import tier_of


def _item_href(p: dict) -> str:
    """Public STAC item URL for an edition member. Resolved through its REAL top-level collection
    group (`sink_stac.collection_group`) — a quad map isn't always in `ugs-publications`: MD-series
    pubs route to `ugs-mining-district-files` and foreign-publisher pubs (BYU/AAPG/USU/UU, …) route
    to `ugs-external`, independent of edition grouping (which only looks at quad+scale). Hardcoding
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
    for i, (yr, p) in enumerate(ordered):
        sid = (p.get("series_id") or "").strip()
        older = ordered[i - 1][1] if i > 0 else None
        newer = ordered[i + 1][1] if i + 1 < n else None
        out[sid] = {
            "version": yr,
            "deprecated": newer is not None,
            "predecessor_href": _item_href(older) if older else None,
            "successor_href": _item_href(newer) if newer else None,
        }


def edition_graph(pubs: list[dict]) -> dict[str, dict]:
    """{series_id -> edition dict} for every quad map placed unambiguously in a (quad, scale)
    group — `{"version", "deprecated", "predecessor_href", "successor_href"}`, the shape
    `sink_stac.build_item(..., edition=...)` consumes. A pub not covered by any rule below is
    simply absent from the result (see the module docstring)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        quad = (p.get("quad_name") or "").strip()
        if not sid or not quad:
            continue  # no quad_name -> not a quad map (the common case, not an anomaly)
        tier = tier_of(p.get("pub_scale"))
        if tier is None:
            print(f"[editions] WARNING: {sid} (quad={quad!r}) has a missing/unparseable "
                  f"pub_scale ({p.get('pub_scale')!r}) — excluded from edition detection")
            continue
        norm_quad = " ".join(quad.split()).casefold()
        groups[(norm_quad, tier)].append(p)

    out: dict[str, dict] = {}
    for (_norm_quad, tier), members in groups.items():
        quad_label = (members[0].get("quad_name") or "").strip()
        _link_group(members, out, quad=quad_label, tier=tier)
    return out
