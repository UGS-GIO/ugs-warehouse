"""Relationships between the layers and tables of one publication's GIS bundle.

Declared relationship classes come from a geodatabase through `ogrinfo -json`. Others are inferred
within one source dataset (a geodatabase, or a folder of shapefiles) from column names and values:
the name rule is ugs-ingest's FK proposer (`scoreNameMatch`), and the value score is the share of
the source column's distinct values found in the target key. Inferred joins are published without
review, so the acceptance bar is higher than the proposer's.

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
SAMPLE_SIZE = 5000
# A key column with more distinct values than this is not kept in memory, so it is never a target.
KEY_CAP = 100_000
OGRINFO_TIMEOUT_S = 300

RELATIONSHIPS_NOT_READ = "relationships_not_read"
RELATIONSHIP_SKIPPED = "relationship_skipped"
PROFILE_FAILED = "relationships_not_inferred"

_FK_SUFFIX = re.compile(r"^(.+?)(_id|_oid|_pk|_fk|id|oid)$", re.IGNORECASE)
_NOT_KEYS = re.compile(r"^(objectid|fid|shape_.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Column:
    """What inference keeps of one column: hashes of up to SAMPLE_SIZE distinct values, every value's
    hash when the column can be a target key, and whether its non-null values are unique."""
    sample: frozenset[int]
    keys: frozenset[int] | None
    unique: bool


@dataclass(frozen=True)
class Layer:
    label: str
    name: str    # the layer's own name, the table name the name rule reads
    group: str   # inference scope: one geodatabase or one shapefile folder
    columns: dict[str, Column]


def _is_key_type(t) -> bool:
    import pyarrow as pa

    return pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_integer(t)


def profile_column(arr) -> Column | None:
    """The column's sample and key sets, or None when it is not strings or integers or has no
    value. Values compare as text, so integer 7 matches string "7". A key needs at least two
    distinct values: a column holding one value is unique only trivially."""
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
    keys = (frozenset(hash(str(v)) for v in uniq.to_pylist())
            if unique and 2 <= len(uniq) <= KEY_CAP else None)
    return Column(sample, keys, unique)


def profile_frame(df) -> dict[str, Column]:
    """Profiles of a (Geo)DataFrame's string and integer columns, skipping geometry, OBJECTID, FID
    and Shape_* columns."""
    import pandas as pd
    import pyarrow as pa

    geom = getattr(df, "_geometry_column_name", None)
    out: dict[str, Column] = {}
    for name in df.columns:
        s = df[name]
        if name == geom or _NOT_KEYS.match(str(name)) or not (
                pd.api.types.is_integer_dtype(s.dtype) or pd.api.types.is_string_dtype(s.dtype)):
            continue
        col = profile_column(pa.array(s, from_pandas=True))
        if col:
            out[str(name)] = col
    return out


def profile_table(table) -> dict[str, Column]:
    """Profiles of a pyarrow Table's string and integer columns (OBJECTID, FID, Shape_* skipped)."""
    out: dict[str, Column] = {}
    for name in table.column_names:
        if _NOT_KEYS.match(name):
            continue
        col = profile_column(table.column(name))
        if col:
            out[name] = col
    return out


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
    DescriptionOfMapUnits): the tie-break between equally scored keys of one table."""
    m = _FK_SUFFIX.match(col.lower())
    return any(len(f) >= 3 and f in table.lower() for f in _forms(m.group(1) if m else col.lower()))


def infer(layers: list[Layer]) -> list[dict]:
    """Relationships inferred between layers of the same group. A pair is accepted when its name
    score passes NAME_GATE, its overlap reaches ACCEPT_OVERLAP and name + overlap x 0.6 reaches
    ACCEPT_TOTAL. One relationship is kept per origin/destination pair, the best scored, and of a
    pair found in both directions the one whose destination holds more keys."""
    best: dict[tuple[str, str], tuple[tuple, dict]] = {}
    for t in layers:
        for tcol, tc in t.columns.items():
            if tc.keys is None:
                continue
            for s in layers:
                if s.group != t.group or s.label == t.label:
                    continue
                for scol, sc in s.columns.items():
                    name = name_score(scol, tcol, t.name)
                    if name < NAME_GATE:
                        continue
                    overlap = 100 * len(sc.sample & tc.keys) / len(sc.sample)
                    total = name + overlap * 0.6
                    if overlap < ACCEPT_OVERLAP or total < ACCEPT_TOTAL:
                        continue
                    rank = (total, overlap, _affinity(tcol, t.name), len(tc.keys), scol, tcol)
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
    """The source dataset a label names: the prefix before its last `__` (a geodatabase's stem),
    else `root`, the bundle itself. Used where the extracted file's path is no longer known."""
    prefix, sep, _ = label.rpartition("__")
    return prefix if sep and prefix else root


def layer_name(label: str) -> str:
    """The layer's own name within its source dataset, from its label."""
    return label.rpartition("__")[2] or label
