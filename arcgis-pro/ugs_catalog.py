"""Read the UGS warehouse STAC catalog for the ArcGIS Pro toolbox.

Standard library plus pyarrow, which Pro's own Python ships (3.3 and later), so nothing is
installed. No arcpy here: the toolbox (`UGSWarehouse.pyt`) does the map work, and this module stays
testable off Windows.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

CDN_HOST = "maps-assets.geology.utah.gov"
CDN_BUCKET = "warehouse"  # the first path segment, which a path-style cloud connection calls a bucket
STAC = f"https://{CDN_HOST}/{CDN_BUCKET}/stac"
TOPICS = f"{STAC}/ugs-serving-topics"
_CHUNK = 1 << 20
RASTER_THEME = "rasters"
# What the dialog calls each theme; a schema not named here shows title-cased.
THEME_NAMES = {"emp": "Energy and Minerals", "hazards": "Geologic Hazards",
               "mapping": "Geologic Mapping", "wetlands": "Wetlands",
               RASTER_THEME: "Scanned Geologic Maps"}


def theme_name(theme: str) -> str:
    return THEME_NAMES.get(theme) or theme.replace("_", " ").title()
# Names a file geodatabase gives its own fields.
_RESERVED = {"objectid", "shape", "shape_length", "shape_area", "fid"}


@dataclass(frozen=True)
class Layer:
    id: str
    title: str
    theme: str  # the dbt schema for a vector layer; RASTER_THEME for a raster
    keywords: tuple[str, ...] = ()
    href: str = ""  # a raster's COG; a vector layer's data comes from its item
    properties: tuple = ()  # a raster's index properties, as items(), for its metadata
    self_href: str = ""  # a raster's full item; the index leaves out its description

    @property
    def is_raster(self) -> bool:
        return self.theme == RASTER_THEME

    def matches(self, query: str | None) -> bool:
        """True when every word of `query` is in the title, id or a keyword (case-insensitive)."""
        text = " ".join((self.title, self.id, *self.keywords)).lower()
        return all(word in text for word in (query or "").lower().split())

    @property
    def item_url(self) -> str:
        return f"{TOPICS}/{self.theme}/{self.id}/{self.id}.json"

    @property
    def collection_url(self) -> str:
        return f"{TOPICS}/{self.theme}/collection.json"

    @property
    def choice(self) -> str:
        """How the layer reads in the tool's pick list; `id_of` reverses it."""
        return f"{self.title} [{self.id}]"


def id_of(choice: str) -> str:
    return choice.rsplit("[", 1)[-1].rstrip("]")


def cdn_key(href: str) -> str | None:
    """The path of a CDN href below the bucket segment (`geoparquet/x/x.parquet`), else None."""
    base = f"https://{CDN_HOST}/{CDN_BUCKET}/"
    return href.removeprefix(base) if href.startswith(base) else None


def cdn_parts(href: str) -> tuple[str, str] | None:
    """(bucket, key) of any CDN href: its first path segment, which a path-style cloud storage
    connection calls the bucket (`geolmap`), and the rest (`cogs/M-180.cog.tif`). Else None."""
    base = f"https://{CDN_HOST}/"
    bucket, _, key = href.removeprefix(base).partition("/") if href.startswith(base) else ("", "", "")
    return (bucket, key) if bucket and key else None


# The bucket behind the CDN. It is private: reading it straight takes a Google sign-in with read
# access, which the toolbox uses when the machine has one (`google_credentials`).
GCS_BUCKET = "ut-dnr-ugs-maps-prod-public"


def bucket_object(href: str) -> str | None:
    """The bucket object behind a CDN href (`warehouse/geoparquet/x/x.parquet`), else None."""
    base = f"https://{CDN_HOST}/"
    return href.removeprefix(base) if href.startswith(base) else None


def google_credentials() -> str | None:
    """The Google sign-in file on this machine, or None: `GOOGLE_APPLICATION_CREDENTIALS`, else
    the one `gcloud auth application-default login` writes."""
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env and os.path.isfile(env):
        return env
    root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(root, "gcloud", "application_default_credentials.json")
    return path if os.path.isfile(path) else None


def get_json(url: str) -> dict:
    """JSON from `url`, asking for gzip: the catalog indexes are ~25x smaller compressed."""
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def layers(index: dict | None = None) -> list[Layer]:
    """Every serving-topic layer, sorted by theme then title."""
    index = index if index is not None else get_json(f"{TOPICS}/items.json")
    out = []
    for i in index.get("items") or []:
        p = i.get("properties") or {}
        out.append(Layer(i["id"], p.get("title") or i["id"], p.get("ugs:dbt_schema") or "",
                         tuple(p.get("keywords") or ())))
    return sorted(out, key=lambda x: (x.theme, x.title.lower()))


def rasters(index: dict | None = None) -> list[Layer]:
    """Every item with a COG on the CDN (the geologic map scans and ugs-rasters), by title."""
    index = index if index is not None else get_json(f"{STAC}/items.json")
    out = []
    for i in index.get("items") or []:
        cog = (i.get("assets") or {}).get("cog") or {}
        if not cog.get("href", "").startswith(f"https://{CDN_HOST}/"):
            continue
        p = i.get("properties") or {}
        words = (p.get("ugs:series_id"), p.get("ugs:author"), p.get("ugs:scale"), *(p.get("keywords") or ()))
        self_href = next((urllib.parse.urljoin(f"{STAC}/", lk["href"]) for lk in i.get("links") or []
                          if lk.get("rel") == "self" and lk.get("href")), "")
        out.append(Layer(i["id"], p.get("title") or i["id"], RASTER_THEME,
                         tuple(w for w in words if w), cog["href"], tuple(p.items()), self_href))
    return sorted(out, key=lambda x: x.title.lower())


def metadata(props: dict, collection: dict | None = None, source: str = "") -> dict[str, str]:
    """Pro metadata fields (title, summary, description, tags, credits, accessConstraints)."""
    collection = collection or {}
    desc = (props.get("description") or "").strip()
    summary = desc.split(". ")[0].rstrip(".") + "." if desc else ""
    providers = [p.get("name") for p in collection.get("providers") or [] if p.get("name")]
    credits = ", ".join(providers) or props.get("ugs:point_of_contact") or props.get("ugs:author") or ""
    lic = collection.get("license") or ""
    lic_url = next((lk["href"] for lk in collection.get("links") or [] if lk.get("rel") == "license"), "")
    access = " ".join(x for x in (lic, lic_url) if x)
    return {k: v for k, v in {
        "title": props.get("title") or "",
        "summary": summary,
        "description": "\n\n".join(x for x in (desc, f"Source: {source}" if source else "") if x),
        "tags": ", ".join(props.get("keywords") or ()),
        "credits": credits,
        "accessConstraints": access,
    }.items() if v}


def _rgb(color: str) -> tuple[int, int, int] | None:
    c = color.strip().lstrip("#") if isinstance(color, str) else ""
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) if len(c) == 6 else None
    except ValueError:
        return None


def _field(expr) -> str | None:
    """The field a GL value expression reads, through `downcase`, `to-string` and a `coalesce`
    default: `["downcase", ["coalesce", ["get", "f"], ""]]` -> "f"."""
    while isinstance(expr, list) and expr:
        if expr[0] == "get" and len(expr) == 2 and isinstance(expr[1], str):
            return expr[1]
        if expr[0] in ("downcase", "to-string", "to-number", "coalesce") and len(expr) > 1:
            expr = expr[1]
            continue
        return None
    return None


def _field_value(expr) -> tuple[str, object] | None:
    """(field, value) from a GL filter that keeps one value of one field, else None.

    Matches `["==", <get>, v]`, with the `get` optionally wrapped (`downcase`, `to-string`), and
    `["all", ["has", f], ["==", ...]]`.
    """
    if not isinstance(expr, list) or not expr:
        return None
    if expr[0] == "all":
        found = [fv for e in expr[1:] if (fv := _field_value(e))]
        return found[0] if len(found) == 1 else None
    if expr[0] != "==" or len(expr) != 3:
        return None
    field = _field(expr[1])
    return (field, expr[2]) if field else None


def _paint_color(layer: dict):
    paint = layer.get("paint") or {}
    for key in ("fill-color", "line-color", "circle-color"):
        if key in paint:
            return paint[key]
    return None


def classes(style: dict, legend: list[dict] | None = None):
    """(field, [(value, label, rgb)]) for a categorical GL style, or None for anything else.

    Two shapes cover the warehouse styles: one layer per value (a filter per layer), or one layer
    whose color is a `match` on a field. Labels come from the item's legend when it lines up.
    """
    gl = [lyr for lyr in style.get("layers") or [] if lyr.get("type") in ("fill", "line", "circle")]
    labels = [e.get("label") for e in legend or []]
    out: list[tuple[object, str, tuple[int, int, int]]] = []
    field = None
    per_layer = [(_field_value(lyr.get("filter")), _rgb(_paint_color(lyr) or "")) for lyr in gl]
    if per_layer and all(fv and rgb for fv, rgb in per_layer):
        fields = {fv[0] for fv, _ in per_layer}
        if len(fields) == 1:
            field = fields.pop()
            seen = set()
            for i, (fv, rgb) in enumerate(per_layer):
                if fv[1] in seen:  # a second layer for the same value (a casing, a halo)
                    continue
                seen.add(fv[1])
                label = labels[i] if len(labels) == len(per_layer) and labels[i] else str(fv[1])
                out.append((fv[1], label, rgb))
            return field, out
    for lyr in gl:
        found = _expression_classes(_paint_color(lyr))
        if found:
            field, pairs = found
            by_color: dict[tuple, list[str]] = {}
            for e in legend or []:
                if (rgb := _rgb(e.get("color") or "")) and e.get("label"):
                    by_color.setdefault(rgb, []).append(e["label"])
            used = [rgb for _, rgb in pairs]
            for v, rgb in pairs:  # a legend label pairs with a class only when its color is unique
                label = by_color.get(rgb, [None])[0] if len(by_color.get(rgb, [])) == 1 \
                    and used.count(rgb) == 1 else None
                out.append((v, label or str(v), rgb))
            return field, out
    return None


def _expression_classes(color) -> tuple[str, list[tuple[object, tuple]]] | None:
    """(field, [(value, rgb)]) from a `match` or a `coalesce`/`get`/`literal` lookup color."""
    if not isinstance(color, list) or not color:
        return None
    if color[0] == "match" and len(color) >= 5 and (field := _field(color[1])):
        pairs = []
        for i in range(2, len(color) - 2, 2):
            values = color[i] if isinstance(color[i], list) else [color[i]]
            if rgb := _rgb(color[i + 1]) if isinstance(color[i + 1], str) else None:
                pairs.extend((v, rgb) for v in values)
        return (field, pairs) if pairs else None
    # ["coalesce", ["get", <key>, ["literal", {value: color}]], default]
    if color[0] == "coalesce" and len(color) >= 2 and isinstance(color[1], list) \
            and color[1][:1] == ["get"] and len(color[1]) == 3:
        lookup = color[1][2]
        table = lookup[1] if isinstance(lookup, list) and lookup[:1] == ["literal"] else None
        if (field := _field(color[1][1])) and isinstance(table, dict):
            pairs = [(v, rgb) for v, c in table.items() if (rgb := _rgb(c) if isinstance(c, str) else None)]
            return (field, pairs) if pairs else None
    return None


def single_color(style: dict) -> tuple[int, int, int] | None:
    """The color of a one-color style (its first fill, line or circle layer), else None.

    A first layer filtered on an attribute draws only some features, so its color would misstate
    the rest; a filter on geometry type alone still draws every feature of the layer's kind.
    """
    for lyr in style.get("layers") or []:
        if lyr.get("type") in ("fill", "line", "circle"):
            color = _paint_color(lyr)
            subset = lyr.get("filter") and not _geometry_type_only(lyr["filter"])
            return _rgb(color) if isinstance(color, str) and not subset else None
    return None


def _geometry_type_only(expr) -> bool:
    """True for `["==", "$type", "Polygon"]` or `["==", ["geometry-type"], "Polygon"]`."""
    return (isinstance(expr, list) and len(expr) == 3 and expr[0] in ("==", "!=")
            and (expr[1] == "$type" or expr[1] == ["geometry-type"]))


def verify(path: str, size: int | None, checksum: str | None) -> bool:
    """True when the file matches the asset's `file:size` and sha256 `file:checksum`."""
    if size is not None and os.path.getsize(path) != size:
        return False
    if not checksum:
        return True
    if not checksum.startswith("1220"):  # not sha2-256: nothing to compare against
        return True
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest() == checksum.removeprefix("1220")


def download(asset: dict, folder: str, name: str) -> str:
    """Fetch an asset into `folder` (reused when an earlier copy still matches). Returns the path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    size, checksum = asset.get("file:size"), asset.get("file:checksum")
    if os.path.exists(path) and verify(path, size, checksum):
        return path
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(asset["href"], timeout=300) as r, open(tmp, "wb") as fh:
            while chunk := r.read(_CHUNK):
                fh.write(chunk)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if not verify(tmp, size, checksum):
        os.remove(tmp)
        raise OSError(f"{name}: download does not match the catalog's size or checksum")
    os.replace(tmp, path)
    return path


def esri_name(name: str, taken: set[str]) -> str:
    """A column name Pro accepts: starts with a letter, letters/digits/underscores, at most 32."""
    base = re.sub(r"[^A-Za-z0-9_]", "_", name).lstrip("_0123456789") or "field"
    base, out, n = base[:32], base[:32], 1
    while out.lower() in taken:
        n += 1
        out = f"{base[:32 - len(str(n)) - 1]}_{n}"
    taken.add(out.lower())
    return out


def nested_columns(path: str) -> list[str]:
    """A GeoParquet's nested columns other than `bbox`, the covering whose values the flat
    bbox_* columns also carry: what a copy for Pro loses."""
    import pyarrow.parquet as pq

    return [f.name for f in pq.read_schema(path) if f.type.num_fields and f.name != "bbox"]


def pro_ready(path: str) -> str:
    """A copy of a GeoParquet that Pro opens: no nested columns, Esri-valid names. Returns its path.

    Pro rejects nested columns, and GeoParquet 1.1's `bbox` covering is a struct; the flat
    `bbox_*` columns carry the same extent. The copy is reused while it is newer than the source.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = path.removesuffix(".parquet") + ".pro.parquet"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(path):
        return out
    table = pq.read_table(path)
    nested = [f.name for f in table.schema if pa.types.is_nested(f.type)]
    table = table.drop_columns(nested)
    meta = dict(table.schema.metadata or {})
    geo = json.loads(meta.get(b"geo", b"{}"))
    primary = geo.get("primary_column", "geom")
    taken = set(_RESERVED)
    names = [esri_name(n, taken) for n in table.column_names]
    renamed = dict(zip(table.column_names, names))
    cols = {}
    for name, spec in (geo.get("columns") or {}).items():
        spec.pop("covering", None)
        cols[renamed.get(name, name)] = spec
    if geo:
        geo["columns"], geo["primary_column"] = cols, renamed.get(primary, primary)
        meta[b"geo"] = json.dumps(geo).encode()
    table = table.rename_columns(names).replace_schema_metadata(meta)
    pq.write_table(table, out + ".part", compression="zstd")
    os.replace(out + ".part", out)
    return out


def esri_field(arrow_type, column, pro: tuple[int, ...]) -> tuple[str, int | None] | None:
    """(Esri field type, text length) for an Arrow column, or None to leave it out."""
    import pyarrow as pa

    modern = pro >= (3, 2)  # BIGINTEGER and DATEONLY arrived in Pro 3.2
    t = arrow_type
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        import pyarrow.compute as pc

        longest = pc.max(pc.utf8_length(column)).as_py() or 1
        return "TEXT", max(longest, 1)
    if pa.types.is_boolean(t) or t in (pa.int8(), pa.int16(), pa.uint8()):
        return "SHORT", None
    if t in (pa.int32(), pa.uint16()):
        return "LONG", None
    if pa.types.is_integer(t):
        return ("BIGINTEGER" if modern else "DOUBLE"), None
    if t in (pa.float16(), pa.float32()):
        return "FLOAT", None
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "DOUBLE", None
    if pa.types.is_date(t):
        return ("DATEONLY" if modern else "DATE"), None
    if pa.types.is_timestamp(t):
        return "DATE", None
    if pa.types.is_time(t):
        return "TEXT", 16
    return None  # binary and anything nested


def esri_value(v):
    """A Python value an insert cursor accepts."""
    import datetime as dt
    import decimal

    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.time):
        return v.isoformat()
    if isinstance(v, dt.datetime) and v.tzinfo is not None:
        return v.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return v


REPO_RAW = "https://raw.githubusercontent.com/UGS-GIO/ugs-warehouse"
REPO_API = "https://api.github.com/repos/UGS-GIO/ugs-warehouse"
TOOLBOX_FILES = ("UGSWarehouse.pyt", "ugs_catalog.py")
BRANCH_FILE = "UGSWarehouse.branch"  # the branch the last update came from


def toolbox_branch(folder: str) -> str:
    try:
        with open(os.path.join(folder, BRANCH_FILE)) as fh:
            return fh.read().strip() or "main"
    except OSError:
        return "main"


class VersionNotFound(LookupError):
    pass


def _pr_number(version: str) -> str | None:
    v = version.strip().lstrip("#")
    return v if v.isdigit() else None


def _commit(branch: str, timeout: float) -> str:
    """The commit a version points at: a branch (`main`) or a pull request number (`535`).

    raw.githubusercontent.com caches a branch-name URL for minutes after a push; a commit URL
    cannot go stale, so fetching by commit sees a push at once. When the API cannot say, a branch
    falls back to its own (cached) URL.
    """
    if pr := _pr_number(branch):
        try:
            return get_json(f"{REPO_API}/pulls/{pr}")["head"]["sha"]
        except Exception as e:  # noqa: BLE001 - a pull request has no raw URL to fall back to
            raise VersionNotFound(f"pull request {pr}: {e}") from e
    req = urllib.request.Request(f"{REPO_API}/commits/{branch}",
                                 headers={"Accept": "application/vnd.github.sha"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sha = r.read().decode().strip()
        return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else branch
    except Exception:  # noqa: BLE001 - rate limit or offline: the branch URL still works, just cached
        return branch


def _fetch_toolbox(branch: str, timeout: float = 60) -> dict[str, bytes]:
    """Both toolbox files from `branch`. Each must compile, so a truncated or HTML body raises."""
    ref = _commit(branch, timeout)
    fresh = {}
    for name in TOOLBOX_FILES:
        try:
            with urllib.request.urlopen(f"{REPO_RAW}/{ref}/arcgis-pro/{name}", timeout=timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise VersionNotFound(branch) from e
            raise
        if len(body) < 100:  # an empty body compiles; it would wipe the toolbox
            raise ValueError(f"{name} from '{branch}' is only {len(body)} bytes")
        compile(body, name, "exec")
        fresh[name] = body
    return fresh


def _local(folder: str, name: str) -> bytes | None:
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:  # closed at once: Windows will not replace an open file
        return fh.read()


def stale_files(folder: str, branch: str, timeout: float = 2) -> list[str] | None:
    """The toolbox files that differ from `branch`, or None when GitHub could not be checked."""
    try:
        fresh = _fetch_toolbox(branch, timeout)
    except Exception:  # noqa: BLE001 - offline, bad branch: no verdict rather than a false alarm
        return None
    return [n for n, body in fresh.items() if not _same(_local(folder, n), body)]


def _same(local: bytes | None, body: bytes) -> bool:
    """Equal apart from line endings: a Git for Windows checkout has CRLF."""
    return local is not None and local.replace(b"\r\n", b"\n") == body.replace(b"\r\n", b"\n")


def update_toolbox(folder: str, branch: str = "main") -> list[str]:
    """Replace the toolbox files in `folder` with the ones on `branch`. Returns the files changed.

    Both files download and must compile before either is replaced, so a failed or partial update
    leaves the working toolbox alone. The old files are kept as `.bak`.
    """
    fresh = _fetch_toolbox(branch)
    todo = {n: (os.path.join(folder, n), _local(folder, n), b) for n, b in fresh.items()}
    todo = {n: t for n, t in todo.items() if not _same(t[1], t[2])}
    for path, old, body in todo.values():  # write everything first, so a full disk breaks nothing
        if old is not None:
            with open(path + ".bak", "wb") as fh:
                fh.write(old)
        with open(path + ".part", "wb") as fh:
            fh.write(body)
    for path, _, _ in todo.values():
        os.replace(path + ".part", path)
    changed = list(todo)
    with open(os.path.join(folder, BRANCH_FILE), "w") as fh:  # only once both files are in place
        fh.write(branch)
    return changed
