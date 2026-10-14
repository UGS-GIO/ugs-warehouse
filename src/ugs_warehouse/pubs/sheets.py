"""Spreadsheet and CSV attachments -> typed pandas tables, and point layers from their coordinates.

A sheet becomes a table only when it has a header row: the first of its first HEADER_SCAN_ROWS rows
that is mostly distinct, non-numeric text and about as wide as the sheet. Cell values stay typed
(numbers are numbers, dates are dates). A sheet with latitude and longitude columns inside Utah
(plus a margin) also yields a point layer in EPSG:4326; rows with no usable coordinate are counted
in a warning, never moved. UTM easting/northing is used only when the sheet names both a zone and a
datum. Nothing here raises for an unusable sheet: it returns a warning, and the caller records it.
"""
from __future__ import annotations

import csv
import math
import re
from typing import NamedTuple

SHEET_EXTS = (".xlsx", ".xls", ".csv")
HEADER_SCAN_ROWS = 10

# Utah's extent plus a margin, in degrees: a coordinate outside it is not a point in Utah.
UTAH_LAT = (37.0 - 1.0, 42.0 + 1.0)
UTAH_LON = (-114.05 - 1.0, -109.04 + 1.0)

_LAT_NAMES = {"lat", "latitude", "y_dd", "lat_dd", "latitude_dd"}
_LON_NAMES = {"lon", "long", "longitude", "x_dd", "lon_dd", "long_dd", "longitude_dd"}
_EASTING = re.compile(r"^(easting|utm_?e|utm_?x|x_?utm|e_?utm)(_|$)")
_NORTHING = re.compile(r"^(northing|utm_?n|utm_?y|y_?utm|n_?utm)(_|$)")
_ZONE = re.compile(r"(?:^|_)(?:utm_?)?(?:zone|z)_?(\d{1,2})(?:_|$|[ns])")
_DATUMS = {"nad83": 26900, "nad_83": 26900, "nad27": 26700, "nad_27": 26700,
           "wgs84": 32600, "wgs_84": 32600}


class Sheet(NamedTuple):
    """One sheet or CSV read: `frame` is None when it has nothing to extract, and `warnings`
    says why or what was changed."""
    frame: object
    warnings: list[str]


def _norm(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or bool(v != v)


def _number_text(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def _engine(path: str) -> str:
    """The pandas Excel engine from the file's first bytes, not its extension, since `.xls` files
    that are really xlsx (and the reverse) are common."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head == b"PK\x03\x04":
        return "openpyxl"
    if head == b"\xd0\xcf\x11\xe0":
        return "xlrd"
    raise ValueError("not an Excel workbook (neither xlsx nor xls content)")


def sheet_names(path: str) -> list[str | None]:
    """The sheets of a workbook, or `[None]` for a CSV. Raises for an unreadable file."""
    import pandas as pd

    if path.lower().endswith(".csv"):
        return [None]
    with pd.ExcelFile(path, engine=_engine(path)) as xl:
        return list(xl.sheet_names)


def _raw(path: str, sheet: str | None, warnings: list[str]):
    """Every cell of the sheet as an object-typed frame with no header, so numbers and dates
    keep their Python types."""
    import pandas as pd

    if sheet is not None:
        return pd.read_excel(path, sheet_name=sheet, header=None, dtype=object,
                             engine=_engine(path))
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=encoding) as fh:
                text = fh.read()
        except UnicodeDecodeError:
            continue
        if encoding != "utf-8-sig":
            warnings.append("CSV is not UTF-8; read as cp1252")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [[(c if c.strip() else None) for c in r] for r in csv.reader(
            text.splitlines(), dialect)]
        width = max((len(r) for r in rows), default=0)
        return pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object)
    raise ValueError("CSV is neither UTF-8 nor cp1252 text")


def _is_header(row: list, widest: int) -> bool:
    cells = [v for v in row if not _blank(v)]
    if not cells or len(cells) < math.ceil(widest / 2):
        return False
    texts = [v.strip() for v in cells if isinstance(v, str) and not _number_text(v.strip())]
    return (len(texts) >= 0.8 * len(cells)
            and len({t.casefold() for t in texts}) >= 0.5 * len(texts))


def dedupe_columns(names: list[str], reserved: tuple[str, ...] = ()
                   ) -> tuple[list[str], list[tuple[str, str]]]:
    """`names` with case-insensitive repeats suffixed `_2`, `_3`, ...; a name equal to one of
    `reserved` counts as taken. A new name never equals another column's own name. Returns
    (new names, [(old, new)] for each rename)."""
    taken = {r.casefold() for r in reserved}
    own = {n.casefold() for n in names}
    out: list[str] = []
    renames: list[tuple[str, str]] = []
    for name in names:
        new, n = name, 1
        while new.casefold() in taken or (new != name and new.casefold() in own):
            n += 1
            new = f"{name}_{n}"
        taken.add(new.casefold())
        out.append(new)
        if new != name:
            renames.append((name, new))
    return out, renames


def rename_warning(renames: list[tuple[str, str]]) -> str:
    return "duplicate column names renamed: " + ", ".join(f"{a} -> {b}" for a, b in renames)


def _header_name(v: object, position: int) -> str:
    if _blank(v):
        return f"column_{position + 1}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def _kind(v: object) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return "number"
    return "date" if hasattr(v, "year") and hasattr(v, "month") else type(v).__name__


def _typed(body, from_text: bool, warnings: list[str]):
    """Give each column one type. A text column of numbers (CSV) becomes numeric unless a value
    has a leading zero (an identifier); a column that mixes types is kept as text and named in a
    warning."""
    import pandas as pd

    mixed: list[str] = []
    for col in body.columns:
        s = body[col]
        values = [v for v in s if not _blank(v)]
        if from_text and values and all(isinstance(v, str) for v in values) and not any(
                re.match(r"^[+-]?0\d", v.strip()) for v in values) and all(
                _number_text(v.strip()) for v in values):
            body[col] = pd.to_numeric(s.map(lambda v: None if _blank(v) else v.replace(",", "")))
            continue
        kinds = {_kind(v) for v in values}
        if len(kinds) > 1:
            body[col] = s.map(lambda v: None if _blank(v) else str(v))
            mixed.append(str(col))
    if mixed:
        warnings.append("columns mixing numbers, dates or text kept as text: " + ", ".join(mixed))
    return body.infer_objects()


def _stacked_header(rows: list[list], start: int) -> tuple[list, int]:
    """The header cells and the last header row, when up to two text-only rows under the header at
    `start` continue it: a row of repeated values is units (`SiO2` -> `SiO2 (%)`), and a row whose
    cells all sit under blank header cells supplies those names."""
    header, end = list(rows[start]), start
    for row in rows[start + 1:start + 3]:
        cells = [(j, v) for j, v in enumerate(row) if not _blank(v)]
        if not cells or any(not isinstance(v, str) or _number_text(v.strip()) for _, v in cells):
            break
        cells = [(j, v.strip()) for j, v in cells]
        if len({v.casefold() for _, v in cells}) < 0.5 * len(cells):
            header = [f"{h} ({row[j].strip()})" if not _blank(h) and not _blank(row[j]) else h
                      for j, h in enumerate(header)]
        elif all(_blank(header[j]) for j, _ in cells):
            for j, v in cells:
                header[j] = v
        else:
            break
        end += 1
    return header, end


def read(path: str, sheet: str | None) -> Sheet:
    """Read one sheet (or a CSV, `sheet` None) as a typed table. Blank rows and columns without a
    header are dropped, duplicate column names renamed. Returns the frame, or None with a warning
    when the sheet is empty, has no detectable header row, or has a header and no rows."""
    warnings: list[str] = []
    raw = _raw(path, sheet, warnings)
    rows = raw.values.tolist()
    if all(_blank(v) for r in rows for v in r):
        return Sheet(None, [*warnings, "sheet is empty; not extracted"])
    scan = rows[:HEADER_SCAN_ROWS]
    widest = max(sum(not _blank(v) for v in r) for r in scan)
    start = next((i for i, r in enumerate(scan) if _is_header(r, widest)), None)
    if start is None:
        return Sheet(None, [*warnings, f"no header row in the first {HEADER_SCAN_ROWS} rows "
                                       "(distinct text cells across the sheet); not extracted"])
    header, end = _stacked_header(rows, start)
    if end > start:
        warnings.append(f"rows {start + 1}-{end + 1} read as one header (units or labels under "
                        "the column names)")
    body = raw.iloc[end + 1:].reset_index(drop=True)
    keep = [i for i in range(raw.shape[1])
            if not _blank(header[i]) or not body.iloc[:, i].map(_blank).all()]
    names, renames = dedupe_columns([_header_name(header[i], i) for i in keep])
    body = body.iloc[:, keep]
    body.columns = names
    body = body[~body.apply(lambda r: all(_blank(v) for v in r), axis=1)].reset_index(drop=True)
    body = body.map(lambda v: None if _blank(v) else v)
    if body.empty:
        return Sheet(None, [*warnings, "header row but no data rows; not extracted"])
    if renames:
        warnings.append(rename_warning(renames))
    return Sheet(_typed(body, sheet is None, warnings), warnings)


def _find(columns: list[str], names: set[str]) -> str | None:
    return next((c for c in columns if _norm(c) in names), None)


def _utm_crs(frame, east: str, north: str) -> tuple[str | None, str]:
    """(EPSG code, why not) for UTM columns: the zone and datum come from the two columns' own
    names, or from a `zone` / `datum` column holding one value. Both must be named."""
    text = "_".join(_norm(c) for c in (east, north))
    zone = _ZONE.search(text)
    datum = next((d for d in _DATUMS if d in text), None)
    for col in frame.columns:
        values = {str(v).strip() for v in frame[col] if not _blank(v)}
        if len(values) != 1:
            continue
        only = _norm(next(iter(values)))
        if _norm(col) in ("zone", "utm_zone") and not zone:
            zone = re.search(r"(\d{1,2})", only)
        elif _norm(col) == "datum" and not datum:
            datum = next((d for d in _DATUMS if d in only), None)
    if not (zone and datum):
        missing = " and ".join(n for n, v in (("zone", zone), ("datum", datum)) if not v)
        return None, f"UTM easting/northing columns found but the sheet names no {missing}"
    return f"EPSG:{_DATUMS[datum] + int(zone.group(1))}", ""


def points(frame, warnings: list[str]):
    """A GeoDataFrame of the rows of `frame` with a coordinate inside Utah (plus a margin), in
    EPSG:4326, or None. Latitude/longitude columns are matched by name; UTM columns only when the
    sheet names a zone and datum. Adds a warning for ignored columns or skipped rows."""
    import geopandas as gpd
    import pandas as pd

    cols = [str(c) for c in frame.columns]
    lat, lon = _find(cols, _LAT_NAMES), _find(cols, _LON_NAMES)
    crs = "EPSG:4326"
    if not (lat and lon):
        east = next((c for c in cols if _EASTING.match(_norm(c))), None)
        north = next((c for c in cols if _NORTHING.match(_norm(c))), None)
        if not (east and north):
            return None
        crs, why = _utm_crs(frame, east, north)
        if not crs:
            warnings.append(f"{why}; table only")
            return None
        lon, lat = east, north
    x = pd.to_numeric(frame[lon], errors="coerce")
    y = pd.to_numeric(frame[lat], errors="coerce")
    if crs != "EPSG:4326":
        from pyproj import Transformer

        usable = x.notna() & y.notna()
        tx, ty = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
            x[usable].to_numpy(), y[usable].to_numpy())
        x, y = x.copy(), y.copy()
        x[usable], y[usable] = tx, ty
    blank = x.isna() | y.isna()
    inside = (~blank & x.between(*UTAH_LON) & y.between(*UTAH_LAT))
    where = f"{lat}/{lon}"
    if not inside.any():
        warnings.append(f"coordinate columns {where} found but no row falls inside Utah; "
                        "table only")
        return None
    skipped = len(frame) - int(inside.sum())
    if skipped:
        warnings.append(f"{skipped} of {len(frame)} rows left out of the point layer: "
                        f"{int(blank.sum())} with blank or non-numeric {where}, "
                        f"{skipped - int(blank.sum())} outside Utah (plus a 1 degree margin)")
    keep = frame[inside].reset_index(drop=True)
    names, renames = dedupe_columns([str(c) for c in keep.columns], reserved=("geometry",))
    keep.columns = names
    if renames:
        warnings.append(rename_warning(renames))
    return gpd.GeoDataFrame(keep, geometry=gpd.points_from_xy(x[inside], y[inside]),
                            crs="EPSG:4326")
