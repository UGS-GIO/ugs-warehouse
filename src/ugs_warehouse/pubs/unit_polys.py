"""A publication's geologic unit polygons in one shape, `unit_polys`, built from its extracted layers.

Publications carry their unit polygons in one of three attribute schemas:

- GeMS: a `MapUnitPolys` layer holding only `MapUnit`; name, age and description are in the
  `DescriptionOfMapUnits` table, joined on the manifest's relationship.
- 2010s template: a `*GeologicUnits` layer with `UnitSymbol`, `UnitName`, `Age_Strat`, `Descriptio`.
- Coverage era: a `geounits`, `units` or `geology_poly(s)` layer with `UNITSYMBOL`, `UNITNAME`,
  `AGE`, `NOTES`.

`select` picks the unit layer from the manifest by layer name and unit-code column (the other
polygon layers a bundle holds, such as landslides or owners, are never unit layers). `build`
writes `<SID>/unit_polys.parquet` (EPSG:4326; series_id, unit_code, unit_name, age, description,
source_label) and `<SID>/unit_polys.pmtiles` (tile layer `units`), and records both in the
manifest. The identify layers under the mosaics and the plate Items' `units` asset read these.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass

from ..core import config, gcs
from ..vector import sink_archive, sink_pmtiles, transform
from . import relations

# Bump to build `unit_polys` again for every pub the next vectors run visits.
UNIT_POLYS_VERSION = 1

TILE_LAYER = "units"
COLUMNS = ("series_id", "unit_code", "unit_name", "age", "description", "source_label")

UNIT_POLYS_FAILED = "unit_polys_failed"
UNIT_LAYER_AMBIGUOUS = "unit_layer_ambiguous"
UNIT_LAYER_UNUSABLE = "unit_layer_unusable"
UNIT_DMU_MISSING = "unit_dmu_missing"
UNIT_DMU_UNRELATED = "unit_dmu_unrelated"
UNIT_POLYS_EMPTY = "unit_polys_empty"
WARNING_CODES = (UNIT_POLYS_FAILED, UNIT_LAYER_AMBIGUOUS, UNIT_LAYER_UNUSABLE, UNIT_DMU_MISSING,
                 UNIT_DMU_UNRELATED, UNIT_POLYS_EMPTY)

# Memory cap for one pub's DuckDB export; a pub's unit layer is at most tens of MB.
DUCKDB_MEMORY = "1GB"


@dataclass(frozen=True)
class Schema:
    """One attribute schema: the layer-name pattern (matched on the lower-cased layer name, anchored
    at its end so a quad name in the prefix never matters) and the columns that hold each field."""
    name: str
    pattern: re.Pattern
    code: tuple[str, ...]
    unit_name: tuple[str, ...]
    age: tuple[str, ...]
    description: tuple[str, ...]


# Column candidates in priority order. A column matches ignoring case and underscores, so
# `UnitSymbol`, `UNITSYMBOL` and `Unit_Symbol` are one candidate. `Descriptio` is the 10-character
# truncation a shapefile gives `Description`.
CODE = ("UnitSymbol", "UNITSYMBOL", "Unit_Symbol", "MapUnit")
NAME = ("UnitName", "UNITNAME", "Unit_Name")
AGE = ("Age_Strat", "Age", "AGE")
DESCRIPTION = ("Descriptio", "Description", "NOTES", "NOTE")

# Ranked: GeMS wins a tie, then the template, then the coverage era.
GEMS = Schema("gems", re.compile(r"mapunitpolys$"), ("MapUnit",), ("Name",), ("Age",), ("Description",))
TEMPLATE = Schema("template", re.compile(r"geologicunits?(?:_nad\d{2}|_\d{8})?$"),
                  CODE, NAME, AGE, DESCRIPTION)
COVERAGE = Schema("coverage", re.compile(r"(?:^|[_-])(?:geounits?|units?|geology_?polys?)$"),
                  CODE, NAME, AGE, DESCRIPTION)
SCHEMAS = (GEMS, TEMPLATE, COVERAGE)

DMU_NAME = "descriptionofmapunits"
FULL_NAME = ("FullName",)
TRUNCATED_LENGTH = 10   # a shapefile column name is cut at 10 characters


@dataclass(frozen=True)
class Selection:
    """The unit layer a manifest names and where each output field comes from. For GeMS the name,
    age and description columns are in `attr_label`, the DescriptionOfMapUnits table, joined on
    `key` (layer column) = `attr_key` (table column); otherwise `attr_label` is the layer itself."""
    label: str
    schema: str
    code_column: str
    attr_label: str
    key: str | None
    attr_key: str | None
    name_column: str | None
    full_name_column: str | None
    age_column: str | None
    description_column: str | None

    def record(self) -> dict:
        return {"label": self.label, "schema": self.schema, "code_column": self.code_column}


def _norm(name: str) -> str:
    return name.replace("_", "").casefold()


def _column(columns: list[str], wanted: tuple[str, ...] | str) -> str | None:
    """The layer's spelling of the first of the `wanted` candidates it has, ignoring case and
    underscores."""
    for want in (wanted,) if isinstance(wanted, str) else wanted:
        hit = next((c for c in columns if _norm(c) == _norm(want)), None)
        if hit:
            return hit
    return None


def _has_polygons(entry: dict) -> bool:
    return bool(entry.get("spatial")) and bool(entry.get("rows")) and any(
        "Polygon" in g for g in entry.get("geometry_types") or [])


def _names(entry: dict) -> list[str]:
    return [c["name"] for c in entry.get("columns") or []]


def _warning(code: str, label: str | None, text: str) -> dict:
    return {"code": code, **({"label": label} if label else {}), "warning": text}


def _dmu_join(manifest: dict, unit: dict, unit_code: str) -> tuple[dict | None, str | None, dict | None]:
    """(the DescriptionOfMapUnits entry, its join columns {key, attr_key}, a warning) for a GeMS
    unit layer. The manifest's relationship between the two decides the columns; without one, the
    GeMS convention (`MapUnit` on both sides) is used and a warning says so."""
    label = unit["label"]
    dmus = [e for e in manifest.get("layers") or []
            if not e.get("spatial") and relations.layer_name(e["label"]).casefold() == DMU_NAME]
    if not dmus:
        return None, None, _warning(UNIT_DMU_MISSING, label,
                                    "no DescriptionOfMapUnits table; unit names, ages and "
                                    "descriptions are empty")
    by_label = {e["label"]: e for e in dmus}
    for rel in manifest.get("relationships") or []:
        ends = {rel.get("origin"): rel.get("origin_cols"), rel.get("destination"): rel.get("destination_cols")}
        other = next((lb for lb in ends if lb in by_label), None)
        if label in ends and other and len(ends[label] or []) == 1 and len(ends[other] or []) == 1:
            return by_label[other], {"key": ends[label][0], "attr_key": ends[other][0]}, None
    # No recorded relationship: the pub's own DMU (same source dataset) joined on MapUnit.
    source = unit.get("source") or relations.source_of_label(label, "")
    same = [e for e in dmus if (e.get("source") or relations.source_of_label(e["label"], "")) == source]
    dmu = (same or dmus)[0]
    key, attr_key = _column(_names(unit), unit_code), _column(_names(dmu), GEMS.code)
    if key and attr_key:
        return dmu, {"key": key, "attr_key": attr_key}, _warning(
            UNIT_DMU_UNRELATED, label,
            f"no recorded relationship to {dmu['label']}; joined on {key} = {attr_key}")
    return None, None, _warning(UNIT_DMU_UNRELATED, label,
                                f"no recorded relationship to {dmu['label']} and no MapUnit column "
                                "to join on; unit names, ages and descriptions are empty")


def select(manifest: dict) -> tuple[Selection | None, list[dict]]:
    """(the unit layer, warnings) for a manifest. A layer qualifies when it has polygon rows, a
    name matching one schema (`SCHEMAS`) and that schema's unit-code column. Among several, the
    schema rank decides (GeMS, template, coverage), then the most rows, then the fewest 10-character
    column names (a shapefile copy truncates them, a geodatabase copy does not), then the label; the
    others are named in a UNIT_LAYER_AMBIGUOUS warning. A layer that matches a name but lacks
    the code column is named in a UNIT_LAYER_UNUSABLE warning when nothing qualifies."""
    warnings: list[dict] = []
    found: list[tuple[int, int, int, str, Schema, dict, str]] = []
    unusable: list[str] = []
    for entry in manifest.get("layers") or []:
        if not _has_polygons(entry):
            continue
        name = relations.layer_name(entry["label"]).casefold()
        for rank, schema in enumerate(SCHEMAS):
            if not schema.pattern.search(name):
                continue
            code = _column(_names(entry), schema.code)
            if code:
                cut = sum(len(c) == TRUNCATED_LENGTH for c in _names(entry))
                found.append((rank, -entry["rows"], cut, entry["label"], schema, entry, code))
            else:
                unusable.append(entry["label"])
            break
    if not found:
        if unusable:
            warnings.append(_warning(UNIT_LAYER_UNUSABLE, None,
                                     f"polygon layers named like unit layers lack a unit-code "
                                     f"column: {', '.join(sorted(unusable))}"))
        return None, warnings
    found.sort(key=lambda f: f[:4])
    _, _, _, label, schema, entry, code = found[0]
    # The same layer listed under a bare and a folder-prefixed label has equal rows and is not a choice.
    if any(f[:2] != found[0][:2] for f in found[1:]):
        warnings.append(_warning(
            UNIT_LAYER_AMBIGUOUS, label,
            f"{len(found)} candidate unit layers; chose {label} ({schema.name}), left out "
            f"{', '.join(f[3] for f in found[1:])}"))
    if schema is GEMS:
        dmu, join, warn = _dmu_join(manifest, entry, code)
        if warn:
            warnings.append(warn)
        names = _names(dmu) if dmu else []
        return Selection(
            label, schema.name, code, dmu["label"] if dmu else label,
            join["key"] if join else None, join["attr_key"] if join else None,
            _column(names, schema.unit_name), _column(names, FULL_NAME),
            _column(names, schema.age), _column(names, schema.description)), warnings
    names = _names(entry)
    return Selection(
        label, schema.name, code, label, None, None, _column(names, schema.unit_name), None,
        _column(names, schema.age), _column(names, schema.description)), warnings


def _clean(expr: str) -> str:
    """A text column as trimmed text, blanks as NULL."""
    return f"NULLIF(TRIM(CAST({expr} AS VARCHAR)), '')"


def _text(table: str, column: str | None) -> str:
    from .vectors import _ident

    return _clean(f"{table}.{_ident(column)}") if column else "CAST(NULL AS VARCHAR)"


def units_sql(sel: Selection, sid: str, layer_path: str, attr_path: str | None, geom_expr: str,
              geom_col: str) -> str:
    """The SELECT that yields the `unit_polys` rows (COLUMNS plus `geom`, lon/lat, 2D) from the
    unit layer's local GeoParquet `layer_path` and, for GeMS, the DescriptionOfMapUnits table's
    `attr_path`. Rows with no geometry are left out."""
    from .vectors import _ident, _literal

    geom = f"u.{_ident(geom_col)}"
    base = (f"read_parquet({_literal(layer_path)}) u")
    if sel.schema == GEMS.name:
        if attr_path and sel.key and sel.attr_key:
            # One row per unit code, so a repeated DMU row never repeats a polygon.
            dmu = (f"(SELECT {_clean(_ident(sel.attr_key))} AS k, "
                   f"any_value({_clean(_ident(sel.name_column)) if sel.name_column else 'NULL'}) AS n, "
                   f"any_value({_clean(_ident(sel.full_name_column)) if sel.full_name_column else 'NULL'}) AS fn, "
                   f"any_value({_clean(_ident(sel.age_column)) if sel.age_column else 'NULL'}) AS a, "
                   f"any_value({_clean(_ident(sel.description_column)) if sel.description_column else 'NULL'}) AS d "
                   f"FROM read_parquet({_literal(attr_path)}) GROUP BY k)")
            join = f"LEFT JOIN {dmu} m ON {_clean('u.' + _ident(sel.key))} = m.k"
            attrs = "COALESCE(m.n, m.fn) AS unit_name, m.a AS age, m.d AS description"
        else:
            join = ""
            attrs = ("CAST(NULL AS VARCHAR) AS unit_name, CAST(NULL AS VARCHAR) AS age, "
                     "CAST(NULL AS VARCHAR) AS description")
    else:
        join = ""
        attrs = (f"{_text('u', sel.name_column)} AS unit_name, {_text('u', sel.age_column)} AS age, "
                 f"{_text('u', sel.description_column)} AS description")
    return (f"SELECT {_literal(sid)} AS series_id, {_text('u', sel.code_column)} AS unit_code, {attrs}, "
            f"{_literal(sel.label)} AS source_label, ST_Force2D({geom_expr}) AS geom "
            f"FROM {base} {join} WHERE {geom} IS NOT NULL AND NOT ST_IsEmpty({geom})")


def _download(series_id: str, label: str, dest: str) -> str:
    from .vectors import VECTORS_PREFIX

    path = os.path.join(dest, f"{len(os.listdir(dest))}_{label[:40]}.parquet".replace("/", "_"))
    gcs.download(f"{VECTORS_PREFIX}/{series_id}/{label}.parquet", path)
    return path


def build(series_id: str, manifest: dict) -> None:
    """Build and upload the pub's `unit_polys` GeoParquet and PMTiles from its extracted unit
    layer, and record `unit_layer`, `unit_polys` and `unit_polys_version` in `manifest`. A pub with
    no unit layer, or one with no polygon geometry, records the version alone, so it is not tried
    again until `UNIT_POLYS_VERSION` changes. Earlier unit warnings are replaced. Raises on any
    read, join or upload failure, leaving the version unset so the next heal tries again."""
    from . import vectors

    manifest["warnings"] = [w for w in manifest.get("warnings") or []
                            if w.get("code") not in WARNING_CODES]
    manifest.pop("unit_layer", None)
    manifest.pop("unit_polys", None)
    labels = {e["label"] for e in manifest.get("layers") or []}
    if "unit_polys" in labels:
        raise ValueError("a layer is named unit_polys, which the unit product takes for its files")
    sel, warnings = select(manifest)
    manifest["warnings"] += warnings
    if sel is None:
        manifest["unit_polys_version"] = UNIT_POLYS_VERSION
        return
    entry = next(e for e in manifest["layers"] if e["label"] == sel.label)
    with tempfile.TemporaryDirectory(prefix=f"u_{series_id.replace('/', '_')}_") as work:
        layer = _download(series_id, sel.label, work)
        attrs = _download(series_id, sel.attr_label, work) if sel.schema == GEMS.name and sel.key else None
        columns, geo = vectors._schema(layer)
        geo = geo or {}
        col = geo.get("primary_column")
        if not col:
            raise ValueError("the unit layer's GeoParquet has no geometry column in its metadata")
        crs = vectors._source_crs(entry, geo, col)
        geom = f"u.{vectors._ident(col)}"
        wgs84 = geom if crs.upper() == "EPSG:4326" else (
            f"ST_Transform({geom}, {vectors._literal(crs)}, 'EPSG:4326', always_xy := true)")
        import duckdb

        con = duckdb.connect()
        try:
            transform.setup(con)
            con.execute(f"SET max_memory={vectors._literal(DUCKDB_MEMORY)}")
            con.execute(f"SET temp_directory={vectors._literal(work)}")
            con.execute(f"CREATE TEMP VIEW units AS {units_sql(sel, series_id, layer, attrs, wgs84, col)}")
            rows = con.execute("SELECT count(*) FROM units").fetchone()[0]
            if rows == 0:
                manifest["warnings"].append(_warning(
                    UNIT_POLYS_EMPTY, sel.label, "every geometry in the unit layer is null or empty"))
                manifest["unit_layer"] = sel.record()
                manifest["unit_polys_version"] = UNIT_POLYS_VERSION
                return
            parquet = os.path.join(work, "unit_polys.parquet")
            geo_json = sink_archive._geo_metadata(con, "units")
            con.execute(
                f"COPY (SELECT * REPLACE (ST_AsWKB(geom) AS geom), "
                f"{{'xmin': ST_XMin(geom), 'ymin': ST_YMin(geom), 'xmax': ST_XMax(geom), "
                f"'ymax': ST_YMax(geom)}} AS bbox FROM units) TO {vectors._literal(parquet)} "
                f"(FORMAT PARQUET, COMPRESSION ZSTD, GEOPARQUET_VERSION 'NONE', "
                f"KV_METADATA {{geo: {vectors._literal(geo_json)}}})")
            geojsonl = os.path.join(work, "unit_polys.geojsonl")
            sink_pmtiles.write_geojsonl(con, "units", geojsonl)
        finally:
            con.close()
        pmtiles = os.path.join(work, "unit_polys.pmtiles")
        sink_pmtiles.run_tippecanoe(geojsonl, pmtiles, TILE_LAYER, what=f"{series_id}/unit_polys",
                                    extra_opts=["-t", work, *sink_pmtiles.IDENTIFY_OPTS])
        os.remove(geojsonl)
        pq_meta = gcs.upload(parquet, vectors.unit_polys_object(series_id),
                             content_type=config.PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
        pm_meta = gcs.upload(pmtiles, vectors.unit_polys_pmtiles_object(series_id),
                             content_type=config.PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
    manifest["unit_layer"] = sel.record()
    manifest["unit_polys"] = {
        "rows": rows, "bytes": pq_meta.size, "checksum": pq_meta.checksum,
        "pmtiles": {"bytes": pm_meta.size, "checksum": pm_meta.checksum}}
    manifest["unit_polys_version"] = UNIT_POLYS_VERSION
