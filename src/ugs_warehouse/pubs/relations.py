"""Relationships between the layers and tables of one publication's GIS bundle.

Declared relationship classes come from a geodatabase through `ogrinfo -json`. Others are inferred
within one source dataset (a geodatabase, or a folder of shapefiles) from column names and values:
the name rule is ugs-ingest's FK proposer (`scoreNameMatch`), and the value score is the share of
the source column's distinct values found in the target key. Inferred joins are published without
review, so the acceptance bar is higher than the proposer's, and they target only non-spatial
tables: a spatial layer is a destination only when the geodatabase declares it.

A relationship runs from its origin, the table holding the foreign key (MapUnitPolys), to its
destination, the keyed table (DescriptionOfMapUnits).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

NAME_GATE = 25
ACCEPT_TOTAL = 80
ACCEPT_OVERLAP = 90.0
# A second, lower bar for a source with only a generic `*ID` name: every sampled value, at least
# MIN_SHARED of them, found in a target key named `*_ID` / `*ID` (LocationSourceID -> DataSources_ID).
LOW_NAME_GATE = 10
MIN_SHARED = 3
SAMPLE_SIZE = 5000
# A target key needs this many distinct values: with fewer, a flag (Y/N) or a code (1..2) on a small
# layer is unique by accident.
MIN_KEYS = 3
# A key column with more distinct values than this is not kept in memory, so it is never a target.
KEY_CAP = 100_000
OGRINFO_TIMEOUT_S = 300

RELATIONSHIPS_NOT_READ = "relationships_not_read"
RELATIONSHIP_SKIPPED = "relationship_skipped"
PROFILE_FAILED = "relationships_not_inferred"
SCOPE_UNKNOWN = "relationships_scope_unknown"

_FK_SUFFIX = re.compile(r"^(.+?)(_id|_oid|_pk|_fk|id|oid)$", re.IGNORECASE)
_ID_KEY = re.compile(r"id$", re.IGNORECASE)
# Row numbers and generic ids that every layer has: never a key or a foreign key.
_NOT_KEYS = re.compile(r"^(objectid|objectid_\d+|fid|fid_.*|id|shape_.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Column:
    """What inference keeps of one column: hashes of up to SAMPLE_SIZE distinct values, every value's
    hash when the column can be a target key, whether its non-null values are unique, and whether
    it is an integer sequence 0..N or 1..N (row numbering, not a code)."""
    sample: frozenset[int]
    keys: frozenset[int] | None
    unique: bool
    dense: bool = False


@dataclass(frozen=True)
class Layer:
    label: str
    name: str    # the layer's own name, the table name the name rule reads
    group: str   # inference scope: one source dataset
    columns: dict[str, Column]
    spatial: bool  # an inferred join never targets a spatial layer


def _is_key_type(t) -> bool:
    import pyarrow as pa

    return pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_integer(t)


def profile_column(arr, keys: bool = True) -> Column | None:
    """The column's sample and, when `keys` and it can be a target, its key set; None when it is
    not strings or integers or has no value. Values compare as text, so integer 7 matches string
    "7". A key is unique, with MIN_KEYS to KEY_CAP distinct values."""
    import pyarrow as pa
    import pyarrow.compute as pc

    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    if pa.types.is_dictionary(arr.type):
        arr = arr.dictionary_decode()
    if not _is_key_type(arr.type):
        return None
    arr = arr.drop_null()
    if not len(arr):
        return None
    uniq = pc.unique(arr)
    unique = len(uniq) == len(arr)
    sample = frozenset(hash(str(v)) for v in uniq.slice(0, SAMPLE_SIZE).to_pylist())
    is_key = keys and unique and MIN_KEYS <= len(uniq) <= KEY_CAP
    dense = False
    if is_key and pa.types.is_integer(arr.type):
        lo, hi = pc.min_max(arr).values()
        dense = (lo.as_py(), hi.as_py()) in ((0, len(uniq) - 1), (1, len(uniq)))
    return Column(sample, frozenset(hash(str(v)) for v in uniq.to_pylist()) if is_key else None,
                  unique, dense)


def _profile(names, column, keys: bool) -> tuple[dict[str, Column], list[str]]:
    out: dict[str, Column] = {}
    failed: list[str] = []
    for name in names:
        try:
            col = profile_column(column(name), keys)
        except Exception as e:
            failed.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if col:
            out[str(name)] = col
    return out, failed


def profile_frame(df, keys: bool = True) -> tuple[dict[str, Column], list[str]]:
    """(profiles of a (Geo)DataFrame's string and integer columns, an error per column that could
    not be profiled). Geometry, OBJECTID, FID, Id and Shape_* columns are skipped. Integer columns
    with nulls arrive from GDAL as float64, so they are never candidates."""
    import pandas as pd
    import pyarrow as pa

    geom = getattr(df, "_geometry_column_name", None)
    names = [n for n in df.columns if n != geom and not _NOT_KEYS.match(str(n)) and (
        pd.api.types.is_integer_dtype(df[n].dtype) or pd.api.types.is_string_dtype(df[n].dtype))]
    return _profile(names, lambda n: pa.array(df[n], from_pandas=True), keys)


def profile_table(table, keys: bool = True) -> tuple[dict[str, Column], list[str]]:
    """`profile_frame` for a pyarrow Table."""
    names = [n for n in table.column_names if not _NOT_KEYS.match(n)]
    return _profile(names, table.column, keys)


def _forms(stem: str) -> set[str]:
    """The stem with its singular and plural, so `DataSourcesID` reaches `DataSource_ID`."""
    if stem.endswith("ies"):
        return {stem, stem[:-3] + "y"}
    if stem.endswith("s"):
        return {stem, stem[:-1]}
    if stem.endswith("y"):
        return {stem, stem[:-1] + "ies", stem + "s"}
    return {stem, stem + "s"}


def name_score(source_col: str, target_col: str, target_table: str) -> int:
    """ugs-ingest `scoreNameMatch`, with each stem rule also tried on the stem's singular/plural."""
    src, pk, tdt = source_col.lower(), target_col.lower(), target_table.lower()
    m = _FK_SUFFIX.match(src)
    stem = m.group(1) if m else src
    forms = _forms(stem)
    if src == pk:
        return 40
    if any(len(f) >= 3 and f in pk for f in forms):
        return 30
    if any(len(f) >= 3 and tdt.endswith(f"_{f}") for f in forms):
        return 28
    if any(len(f) >= 4 and f in tdt for f in forms):
        return 22
    return 10 if m else 0


def _affinity(col: str, table: str) -> bool:
    """True when the key column's stem appears in its table's name (MapUnit in
    DescriptionOfMapUnits)."""
    m = _FK_SUFFIX.match(col.lower())
    return any(len(f) >= 3 and f in table.lower() for f in _forms(m.group(1) if m else col.lower()))


def _accepted(name: int, overlap: float, shared: int, target_col: str) -> bool:
    """The main bar (NAME_GATE, ACCEPT_OVERLAP, ACCEPT_TOTAL), or the low-name bar."""
    if name >= NAME_GATE and overlap >= ACCEPT_OVERLAP and name + overlap * 0.6 >= ACCEPT_TOTAL:
        return True
    return (name >= LOW_NAME_GATE and overlap == 100 and shared >= MIN_SHARED
            and bool(_ID_KEY.search(target_col)))


def infer(layers: list[Layer]) -> list[dict]:
    """Relationships inferred from a layer to a non-spatial table of the same group (`_accepted`).
    A target key that numbers its rows (0..N or 1..N) counts only when its name is its table's
    (`_affinity`). One relationship is kept per origin/destination pair, the best scored: total,
    then overlap, then `_affinity`. Of a pair found in both directions, the one whose destination
    holds more keys is kept."""
    best: dict[tuple[str, str], tuple[tuple, dict]] = {}
    for t in layers:
        if t.spatial:
            continue
        for tcol, tc in t.columns.items():
            if tc.keys is None:
                continue
            affinity = _affinity(tcol, t.name)
            if tc.dense and not affinity:
                continue
            for s in layers:
                if s.group != t.group or s.label == t.label:
                    continue
                for scol, sc in s.columns.items():
                    name = name_score(scol, tcol, t.name)
                    if name < LOW_NAME_GATE:
                        continue
                    shared = len(sc.sample & tc.keys)
                    overlap = 100 * shared / len(sc.sample)
                    if not _accepted(name, overlap, shared, tcol):
                        continue
                    total = name + overlap * 0.6
                    rank = (total, overlap, affinity, len(tc.keys), scol, tcol)
                    rel = {"origin": s.label, "origin_cols": [scol],
                           "destination": t.label, "destination_cols": [tcol],
                           "cardinality": "one_to_one" if sc.unique else "many_to_one",
                           "detected_by": "inferred", "confidence": int(total + 0.5),
                           "overlap": round(overlap, 1), "_keys": len(tc.keys)}
                    pair = (s.label, t.label)
                    if pair not in best or rank > best[pair][0]:
                        best[pair] = (rank, rel)
    out = []
    for (o, d), (_, rel) in sorted(best.items()):
        back = best.get((d, o))
        if back and (back[1]["_keys"], o) > (rel["_keys"], d):
            continue
        out.append({k: v for k, v in rel.items() if k != "_keys"})
    return out


def read_declared(gdb: str) -> tuple[list[dict] | None, str | None]:
    """(the geodatabase's relationship classes as `ogrinfo -json` reports them, None), or (None,
    why they could not be read)."""
    exe = shutil.which("ogrinfo")
    if not exe:
        return None, "ogrinfo is not on PATH"
    try:
        run = subprocess.run([exe, "-json", "-so", gdb], capture_output=True, text=True,
                             timeout=OGRINFO_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"ogrinfo failed: {type(e).__name__}: {e}"
    if run.returncode:
        return None, f"ogrinfo exited {run.returncode}: {run.stderr.strip()[:500]}"
    try:
        doc = json.loads(run.stdout)
    except json.JSONDecodeError as e:
        return None, f"ogrinfo output is not JSON: {e}"
    rels = doc.get("relationships") if isinstance(doc, dict) else None
    if not isinstance(rels, dict):
        return None, "ogrinfo reported no `relationships` (GDAL before 3.6 does not read them)"
    return [{"name": name, **rel} for name, rel in rels.items() if isinstance(rel, dict)], None


# GDAL cardinality -> (side holding the foreign key, keyed side, manifest cardinality). GDAL's left
# table is the relationship's base (keyed) table.
_DECLARED = {"OneToMany": ("right", "left", "many_to_one"),
             "OneToOne": ("right", "left", "one_to_one"),
             "ManyToOne": ("left", "right", "many_to_one")}


def orient_declared(rel: dict, labels: dict[str, str]) -> tuple[dict | None, str | None]:
    """A declared relationship in the manifest's origin -> destination shape, or (None, why it is
    not published). `labels` maps the geodatabase's table names to extracted labels."""
    card = rel.get("cardinality")
    tables = (rel.get("left_table_name") or "", rel.get("right_table_name") or "")
    if rel.get("related_table_type") == "media" or any(t.upper().endswith("__ATTACH") for t in tables):
        return None, "is an attachment relationship, not a data join"
    if card not in _DECLARED or rel.get("mapping_table_name"):
        return None, f"is {card} or uses a mapping table, which is not published as a join"
    fk_side, key_side, cardinality = _DECLARED[card]
    by_fold = {k.casefold(): v for k, v in labels.items()}

    def label(side: str) -> str | None:
        name = rel.get(f"{side}_table_name") or ""
        return labels.get(name) or by_fold.get(name.casefold())

    origin, dest = label(fk_side), label(key_side)
    o_cols, d_cols = rel.get(f"{fk_side}_table_fields") or [], rel.get(f"{key_side}_table_fields") or []
    if not (origin and dest):
        return None, (f"joins {rel.get('left_table_name')!r} and {rel.get('right_table_name')!r}, "
                      "and one was not extracted (empty, unreadable or not a table)")
    if origin == dest:
        return None, "relates a table to itself"
    if not o_cols or len(o_cols) != len(d_cols):
        return None, f"has unmatched key fields {o_cols} -> {d_cols}"
    return {"origin": origin, "origin_cols": list(o_cols), "destination": dest,
            "destination_cols": list(d_cols), "cardinality": cardinality,
            "detected_by": "declared"}, None


def merge(declared: list[dict], inferred: list[dict]) -> list[dict]:
    """Declared relationships, then inferred ones between tables no declared one already joins."""
    joined = {frozenset((r["origin"], r["destination"])) for r in declared}
    return [*declared, *(r for r in inferred
                         if frozenset((r["origin"], r["destination"])) not in joined)]


def layer_kind(spatial: bool, label: str, relationships: list[dict]) -> str:
    """`layer` for a spatial layer, `table` for a non-spatial one in a relationship, else
    `supporting`."""
    if spatial:
        return "layer"
    related = any(label in (r.get("origin"), r.get("destination")) for r in relationships)
    return "table" if related else "supporting"


def source_of_label(label: str, root: str) -> str:
    """The source dataset a label names: the prefix before its last `__` (a geodatabase's stem,
    after the folder it sits in when its name collided; or a collided shapefile's folder), else
    `root`, the bundle itself."""
    prefix, sep, _ = label.rpartition("__")
    return prefix if sep and prefix else root


def collided(labels: set[str]) -> bool:
    """True when a label carries a folder prefix because its name was taken (`sub__faults` beside
    `faults`): the bundle has several folders, so an un-prefixed shapefile's folder is unknown. A
    geodatabase layer named like a shapefile reads the same way, which errs toward unknown."""
    for label in labels:
        start = label.find("__")
        while start > 0:
            if label[start + 2:] in labels:
                return True
            start = label.find("__", start + 2)
    return False


def layer_name(label: str) -> str:
    """The layer's own name within its source dataset, from its label."""
    return label.rpartition("__")[2] or label
