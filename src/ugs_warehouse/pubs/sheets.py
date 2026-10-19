"""Spreadsheet and CSV attachments -> typed pandas tables, and point layers from their coordinates.

A sheet becomes a table only when it has a header row: the first of its first HEADER_SCAN_ROWS rows
that is mostly distinct, non-numeric text and about as wide as the sheet. Cell values stay typed
(numbers are numbers, dates are dates). A sheet with latitude and longitude columns inside Utah
(plus a margin) also yields a point layer in EPSG:4326; rows with no usable coordinate are counted
in a warning, never moved. UTM easting/northing is used only when the sheet names both a zone and a
datum. An unusable sheet returns a warning rather than raising, and the caller records it; a file
that cannot be opened, or is over MAX_SHEET_BYTES, raises with its name.

Workbooks are opened once (`Workbook`) and read row by row with openpyxl in read-only mode (.xlsx)
or xlrd (.xls); a CSV is parsed by Arrow, never as Python objects.
"""
from __future__ import annotations

import csv
import itertools
import math
import os
import re
from typing import NamedTuple

SHEET_EXTS = (".xlsx", ".xls", ".csv")
HEADER_SCAN_ROWS = 10
# A workbook or CSV larger than this is reported as an error naming the file, not loaded.
MAX_SHEET_BYTES = 500 * 2**20

# Utah's extent plus a margin, in degrees: a coordinate outside it is not a point in Utah.
UTAH_LAT = (37.0 - 1.0, 42.0 + 1.0)
UTAH_LON = (-114.05 - 1.0, -109.04 + 1.0)

# Whole-name matches (after `_norm`) for coordinate columns that carry no `lat`/`lon` token.
_LAT_NAMES = {"y_dd"}
_LON_NAMES = {"x_dd"}
_LAT_TOKENS = {"lat", "latitude"}
_LON_TOKENS = {"lon", "long", "longitude"}
_EAST_TOKENS = {"easting", "utme", "utmx"}
_NORTH_TOKENS = {"northing", "utmn", "utmy"}
# A longitude column named as west: a `W`/`WEST` word (`LON_WEST`, `Long W`, `Longitude (W)`, `°W`).
_WEST = re.compile(r"(?:^|[^a-z0-9])w(?:est)?(?:$|[^a-z0-9])", re.I)
_ZONE = re.compile(r"(?:^|_)(?:utm_?)?(?:zone|z)_?(\d{1,2})(?:_|$|[ns])")
_DATUMS = {"nad83": 26900, "nad_83": 26900, "nad27": 26700, "nad_27": 26700,
           "wgs84": 32600, "wgs_84": 32600}
# Units that may sit in a row under the column names.
_UNITS = {"%", "wt%", "wt.%", "pct", "ppm", "ppb", "ppt", "m", "ft", "km", "cm", "mm", "in",
          "mg/l", "ug/l", "g/l", "mg/kg", "ug/g", "meq/l", "deg", "degrees", "ma", "ka", "ga",
          "m3", "psi", "lb", "kg", "g", "mg", "s", "us/cm", "ohm", "mv", "ph", "cfs", "gpm"}


class Sheet(NamedTuple):
    """One sheet or CSV read: `frame` is None when it has nothing to extract, and `warnings`
    says why or what was changed."""
    frame: object
    warnings: list[str]


def _norm(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _tokens(name: object) -> set[str]:
    """The letter-only words of a column name, leaving out anything in parentheses or brackets
    (units) and digits (footnote marks): `Latitude WGS846` -> {latitude, wgs}."""
    bare = re.sub(r"[(\[][^)\]]*[)\]]", " ", str(name).lower())
    return set(re.findall(r"[a-z]+", bare))


def _blank(v: object) -> bool:
    if v is None or (isinstance(v, str) and not v.strip()):
        return True
    try:
        return bool(v != v)
    except TypeError:  # pandas' NA has no truth value; it is missing by definition
        return True


def _number_text(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def _check_size(path: str) -> None:
    size = os.path.getsize(path)
    if size > MAX_SHEET_BYTES:
        raise ValueError(f"{os.path.basename(path)} is {size / 2**20:.0f} MB, over the "
                         f"{MAX_SHEET_BYTES / 2**20:.0f} MB limit for spreadsheets; not read")


class Workbook:
    """An Excel workbook opened once; `names` are its worksheets and `rows(name)` reads one from
    the open handle. The format comes from the file's first bytes, not its extension, since `.xls`
    files that are really xlsx (and the reverse) are common."""

    def __init__(self, path: str) -> None:
        _check_size(path)
        with open(path, "rb") as fh:
            head = fh.read(4)
        self._xls = head == b"\xd0\xcf\x11\xe0"
        if self._xls:
            import xlrd

            self._book = xlrd.open_workbook(path, on_demand=True)
            self.names: list[str] = self._book.sheet_names()
        elif head == b"PK\x03\x04":
            import openpyxl

            self._book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            self.names = [ws.title for ws in self._book.worksheets]
        else:
            raise ValueError(f"{os.path.basename(path)} is not an Excel workbook "
                             "(neither xlsx nor xls content)")

    def rows(self, name: str) -> list[list]:
        """Every row of the sheet, padded to one width. Dates are datetimes; a formula's cached
        value is used; an error cell is blank."""
        if self._xls:
            import xlrd

            sh = self._book.sheet_by_name(name)
            out = []
            for r in range(sh.nrows):
                row: list = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    v = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        v = xlrd.xldate.xldate_as_datetime(v, self._book.datemode)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        v = bool(v)
                    elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        v = None
                    elif cell.ctype == xlrd.XL_CELL_NUMBER and float(v).is_integer():
                        v = int(v)
                    row.append(v)
                out.append(row)
            self._book.unload_sheet(name)
        else:
            ws = self._book[name]
            ws.reset_dimensions()  # a stale dimension tag would truncate the rows read
            out = [list(r) for r in ws.iter_rows(values_only=True)]
        width = max((len(r) for r in out), default=0)
        return [r + [None] * (width - len(r)) for r in out]

    def close(self) -> None:
        if not self._xls:
            self._book.close()
        else:
            self._book.release_resources()

    def __enter__(self) -> Workbook:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def sheet_names(path: str) -> list[str | None]:
    """The sheets of a workbook, or `[None]` for a CSV. Raises for an unreadable or oversized
    file."""
    if path.lower().endswith(".csv"):
        _check_size(path)
        return [None]
    with Workbook(path) as book:
        return list(book.names)


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


def _typed(body, warnings: list[str]):
    """Give each column one type: a column that mixes numbers, dates and text is kept as text and
    named in a warning."""
    mixed: list[str] = []
    for col in body.columns:
        s = body[col]
        if len({_kind(v) for v in s if not _blank(v)}) > 1:
            body[col] = s.map(lambda v: None if _blank(v) else str(v))
            mixed.append(str(col))
    if mixed:
        warnings.append("columns mixing numbers, dates or text kept as text: " + ", ".join(mixed))
    return body.infer_objects()


def _unit_like(v: str) -> bool:
    """A short cell that reads as a unit: a known unit (`ppm`, `mg/L`), or symbols with no letters
    (`%`, `10^-6`)."""
    low = v.strip().lower()
    return len(low) <= 10 and (low in _UNITS or not any(c.isalpha() for c in low))


def _stacked_header(rows: list[list], start: int) -> tuple[list, int]:
    """The header cells and the last header row, when up to two text-only rows under the header at
    `start` continue it: a row of units (`SiO2` -> `SiO2 (%)`), and a row whose cells all sit
    under blank header cells supplies those names."""
    header, end = list(rows[start]), start
    for row in rows[start + 1:start + 3]:
        cells = [(j, v) for j, v in enumerate(row) if not _blank(v)]
        if not cells or any(not isinstance(v, str) or _number_text(v.strip()) for _, v in cells):
            break
        cells = [(j, v.strip()) for j, v in cells]
        if all(_unit_like(v) for _, v in cells):
            header = [f"{h} ({row[j].strip()})" if not _blank(h) and not _blank(row[j]) else h
                      for j, h in enumerate(header)]
        elif all(_blank(header[j]) for j, _ in cells):
            for j, v in cells:
                header[j] = v
        else:
            break
        end += 1
    return header, end


def _header_of(rows: list[list], warnings: list[str]):
    """(header cells, header row, last header row) from the sheet's leading `rows`, or a Sheet
    with nothing to extract."""
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
    return header, start, end


def read(path: str, sheet: str | None, book: Workbook | None = None) -> Sheet:
    """Read one sheet (or a CSV, `sheet` None) as a typed table. Blank rows and columns without a
    header are dropped, duplicate column names renamed. `book` is the open workbook to read from,
    else the file is opened for this call. Returns the frame, or None with a warning when the sheet
    is empty, has no detectable header row, or has a header and no rows."""
    import pandas as pd

    if sheet is None:
        return _read_csv(path)
    warnings: list[str] = []
    if book is None:
        with Workbook(path) as opened:
            rows = opened.rows(sheet)
    else:
        rows = book.rows(sheet)
    found = _header_of(rows, warnings)
    if isinstance(found, Sheet):
        return found
    header, _, end = found
    raw = pd.DataFrame(rows[end + 1:], dtype=object, columns=range(len(header)))
    del rows
    keep = [i for i in range(len(header))
            if not _blank(header[i]) or not raw.iloc[:, i].map(_blank).all()]
    names, renames = dedupe_columns([_header_name(header[i], i) for i in keep])
    body = raw.iloc[:, keep]
    body.columns = names
    body = body[~body.apply(lambda r: all(_blank(v) for v in r), axis=1)].reset_index(drop=True)
    body = body.map(lambda v: None if _blank(v) else v)
    if body.empty:
        return Sheet(None, [*warnings, "header row but no data rows; not extracted"])
    if renames:
        warnings.append(rename_warning(renames))
    return Sheet(_typed(body, warnings), warnings)


def _csv_encoding(path: str, warnings: list[str]) -> str:
    """`utf-8-sig` when the whole file decodes as UTF-8, else `cp1252` with a warning."""
    import codecs

    dec = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                dec.decode(chunk)
        dec.decode(b"", final=True)
        return "utf-8-sig"
    except UnicodeDecodeError:
        pass
    try:
        dec = codecs.getincrementaldecoder("cp1252")()
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                dec.decode(chunk)
    except UnicodeDecodeError as e:
        raise ValueError("CSV is neither UTF-8 nor cp1252 text") from e
    warnings.append("CSV is not UTF-8; read as cp1252")
    return "cp1252"


def _numeric(col):
    """An Arrow string column as int64 or float64 when every value parses and none has a leading
    zero (an identifier such as 007); else unchanged."""
    import pyarrow as pa
    import pyarrow.compute as pc

    if pc.any(pc.match_substring_regex(col, r"^\s*[+-]?0\d")).as_py():
        return col
    for kind in (pa.int64(), pa.float64()):
        try:
            return pc.cast(col, kind)
        except pa.ArrowInvalid:
            continue
    return col


def _check_aligned(path: str, table, lead: list[list], end: int, fields: list[int],
                   width: int) -> None:
    """Raise if Arrow's first data row is not the first one the csv module saw after the header,
    which means the two parsers split the file differently and the table would be garbled."""
    first = next((i for i in range(end + 1, len(lead))
                  if any(c is not None for c in lead[i])), None)
    if first is None or fields[first] != width or table.num_rows == 0:
        return
    got = [(v if v is None or v.strip() else None)
           for v in (table.column(i)[0].as_py() for i in range(width))]
    if got != lead[first][:width]:
        raise ValueError(f"{os.path.basename(path)}: the CSV parsers disagree on where the data "
                         f"starts (expected {lead[first][:width]!r}, read {got!r}); not extracted")


def _read_csv(path: str) -> Sheet:
    """A CSV as a typed table, parsed by Arrow: the first rows find the header, then the rest is
    read as text columns and each is made numeric when every value parses."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pacsv

    _check_size(path)
    warnings: list[str] = []
    enc = _csv_encoding(path, warnings)
    lines = [0]  # physical lines read so far; a record can span several (quoted line breaks)

    def counted(fh):
        for line in fh:
            lines[0] += 1
            yield line

    with open(path, newline="", encoding=enc) as fh:
        sample = fh.read(8192)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        fh.seek(0)
        lead, ends = [], []
        for record in itertools.islice(csv.reader(counted(fh), dialect), 200):
            lead.append([(c if c.strip() else None) for c in record])
            ends.append(lines[0])  # csv reads no further than the record it yields
    fields = [len(r) for r in lead]
    width = max(fields, default=0)
    lead = [r + [None] * (width - len(r)) for r in lead]
    found = _header_of(lead, warnings)
    if isinstance(found, Sheet):
        return found
    header, start, end = found
    width = fields[start]  # the header's field count; a row with another count is ragged
    header = header[:width]
    cols = [f"c{i}" for i in range(width)]
    skipped: list[int] = []

    def ragged(row):
        skipped.append(row.text[:60])
        return "skip"

    table = pacsv.read_csv(
        path,
        read_options=pacsv.ReadOptions(skip_rows=ends[end], column_names=cols,
                                       encoding="utf-8" if enc == "utf-8-sig" else enc),
        parse_options=pacsv.ParseOptions(delimiter=dialect.delimiter,
                                         quote_char=dialect.quotechar or '"',
                                         invalid_row_handler=ragged),
        convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in cols},
                                             strings_can_be_null=True, null_values=[""]))
    if skipped:
        warnings.append(f"{len(skipped)} rows with a different number of fields than the header "
                        f"were left out (first: {skipped[0]!r})")
    _check_aligned(path, table, lead, end, fields, width)
    keep = [i for i in range(width) if not _blank(header[i]) or table[cols[i]].null_count < len(table)]
    names, renames = dedupe_columns([_header_name(header[i], i) for i in keep])
    table = pa.table({n: _numeric(table[cols[i]]) for n, i in zip(names, keep)})
    if table.num_columns:
        some = pc.is_valid(table.column(0))
        for c in table.columns[1:]:
            some = pc.or_(some, pc.is_valid(c))
        table = table.filter(some)
    if table.num_rows == 0:
        return Sheet(None, [*warnings, "header row but no data rows; not extracted"])
    if renames:
        warnings.append(rename_warning(renames))
    return Sheet(table.to_pandas(), warnings)


def _find(columns: list[str], tokens: set[str], names: set[str], other: set[str]) -> list[str]:
    """The columns whose name has one of `tokens` as a word (and no word of the opposite axis
    `other`, as in `Lat/Long`), or equals one of `names`, the plainest names first."""
    hits = [c for c in columns
            if (_tokens(c) & tokens and not _tokens(c) & other) or _norm(c) in names]
    return sorted(hits, key=lambda c: len(_tokens(c)))


def _utm_crs(frame, east: str, north: str) -> tuple[str | None, str]:
    """(EPSG code, why not) for UTM columns: the zone and datum come from the two columns' own
    names, or from a `zone` / `datum` column holding one value. Both must be named."""
    text = "_".join(_norm(c) for c in (east, north))
    zone = _ZONE.search(text)
    datum = next((d for d in _DATUMS if d in text), None)
    for col in frame.columns:
        values = {str(v).strip() for v in frame[col].dropna().unique() if not _blank(v)}
        if len(values) != 1:
            continue
        only = _norm(next(iter(values)))
        if _norm(col) in ("zone", "utm_zone") and not zone:
            zone = re.search(r"(\d{1,2})", only)
        elif _norm(col) == "datum" and not datum:
            datum = next((d for d in _DATUMS if d in only), None)
    if not (zone and datum):
        missing = " and ".join(n for n, v in (("zone", zone), ("datum", datum)) if not v)
        return None, f"UTM columns {east}/{north} found but the sheet does not give the {missing}"
    return f"EPSG:{_DATUMS[datum] + int(zone.group(1))}", ""


def _west_positive(x, name: str, where: str, warnings: list[str]):
    """`x` with its sign flipped when the sheet stores west longitudes as positive numbers: the
    column is named as west and holds no negative value, or every value is positive and lands in
    Utah once negated. Anything else is returned unchanged. A flip is reported as a warning."""
    vals = x.dropna()
    if vals.empty or (vals < 0).any():
        return x
    named = bool(_WEST.search(name) or "°w" in name.lower())
    fits = bool(((-vals).between(*UTAH_LON)).all()) and bool((vals > 0).all())
    if not (named or fits):
        return x
    warnings.append(f"longitudes in {where} read as west-positive and negated")
    return -x


def points(frame, warnings: list[str]):
    """A GeoDataFrame of the rows of `frame` with a coordinate inside Utah (plus a margin), in
    EPSG:4326, or None. Latitude/longitude columns are matched by word (`Latitude WGS846`,
    `Surf Lon`, `Longitude (°W)`); UTM columns only when the sheet names a zone and datum. A column
    that looks like a coordinate and is not used gets a warning naming it."""
    import geopandas as gpd
    import pandas as pd

    cols = [str(c) for c in frame.columns]
    lats = _find(cols, _LAT_TOKENS, _LAT_NAMES, _LON_TOKENS)
    lons = _find(cols, _LON_TOKENS, _LON_NAMES, _LAT_TOKENS)
    crs = "EPSG:4326"
    if lats and lons:
        lat, lon = lats[0], lons[0]
    else:
        easts = _find(cols, _EAST_TOKENS, set(), _NORTH_TOKENS)
        norths = _find(cols, _NORTH_TOKENS, set(), _EAST_TOKENS)
        if easts and norths:
            crs, why = _utm_crs(frame, easts[0], norths[0])
            if not crs:
                warnings.append(f"{why}; table only")
                return None
            lon, lat = easts[0], norths[0]
        else:
            lone = lats + lons + easts + norths
            if lone:
                warnings.append(f"column {lone[0]} looks like a coordinate but has no matching "
                                "partner column; table only")
            return None
    # Plain floats: whole-number UTM columns would otherwise refuse the converted degrees.
    x = pd.to_numeric(frame[lon], errors="coerce").astype("float64")
    y = pd.to_numeric(frame[lat], errors="coerce").astype("float64")
    if crs != "EPSG:4326":
        from pyproj import Transformer

        usable = x.notna() & y.notna()
        tx, ty = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
            x[usable].to_numpy(), y[usable].to_numpy())
        x[usable], y[usable] = tx, ty
    where = f"{lat}/{lon}"
    if crs == "EPSG:4326":
        x = _west_positive(x, lon, where=where, warnings=warnings)
    blank = x.isna() | y.isna()
    inside = (~blank & x.between(*UTAH_LON) & y.between(*UTAH_LAT))
    if not inside.any():
        swapped = bool((~blank & x.between(*UTAH_LAT) & y.between(*UTAH_LON)).any())
        warnings.append(f"coordinate columns {where} found but no row falls inside Utah; "
                        "table only" + ("; latitude and longitude look swapped, not changed"
                                        if swapped else ""))
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
