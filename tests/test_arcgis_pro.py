"""The ArcGIS Pro toolbox: catalog parsing in `ugs_catalog`, and the `.pyt` against a fake arcpy.

Hermetic: no network, no Pro. The fake arcpy records what the toolbox asks of the map, which is
the part a Windows test cannot show quickly: which field it styles by, and with what colors.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "arcgis-pro"
sys.path.insert(0, str(DIR))
import ugs_catalog as cat  # noqa: E402

INDEX = {"items": [
    {"id": "hazards_qfaults", "properties": {"title": "Quaternary Faults", "ugs:dbt_schema": "hazards",
                                             "keywords": ["slip rate", "neotectonics"]}},
    {"id": "enmin_powerplants", "properties": {"title": "Power Plants", "ugs:dbt_schema": "emp"}},
]}

HREF_PLACEHOLDER = "https://maps-assets.geology.utah.gov/warehouse/geoparquet/x/x.parquet"
ROOT = {"items": [  # the root index: one CDN COG, one scan on the legacy host, one vector layer
    {"id": "M-180", "properties": {"title": "Geologic map of the Salt Lake City quadrangle",
                                   "ugs:series_id": "M-180", "ugs:author": "Personius",
                                   "description": "A 1:24,000 map. Scanned."},
     "assets": {"cog": {"href": "https://maps-assets.geology.utah.gov/geolmap/cogs/M-180.cog.tif"}}},
    {"id": "OLD-1", "properties": {"title": "Old scan"},
     "assets": {"publication": {"href": "https://ugspub.nr.utah.gov/x.tif"}}},
    {"id": "hazards_qfaults", "properties": {"title": "Quaternary Faults"},
     "assets": {"data": {"href": HREF_PLACEHOLDER}}},
]}

PER_LAYER = {"layers": [  # one layer per value, as the hazards_qfaults style is written
    {"type": "line", "filter": ["==", ["downcase", ["to-string", ["get", "qffhazardunit"]]], "u150wcqff"],
     "paint": {"line-color": "#e60000"}},
    {"type": "line", "filter": ["==", ["downcase", ["to-string", ["get", "qffhazardunit"]]], "u15kwcqff"],
     "paint": {"line-color": "#E69800"}},
]}
LOOKUP = {"layers": [  # a color looked up from a literal table, as most hazard styles are written
    {"type": "fill", "paint": {"fill-color": ["coalesce", ["get", ["coalesce", ["get", "aafhazardunit"], ""],
                                                           ["literal", {"Haaf": "#ff0000", "Maaf": "#0070ff"}]], "#BDBDBD"]}},
    {"type": "line", "paint": {"line-color": "#6e6e6e"}},
]}
MATCH = {"layers": [{"type": "circle", "paint": {"circle-color": [
    "match", ["downcase", ["to-string", ["get", "primsource"]]], "coal", "#000000", ["wind", "solar"], "#a6cee3", "#999"]}}]}


def test_layers_sort_by_theme_and_round_trip_their_pick_list_entry():
    got = cat.layers(INDEX)
    assert [x.id for x in got] == ["enmin_powerplants", "hazards_qfaults"]
    assert cat.id_of(got[1].choice) == "hazards_qfaults"
    assert got[1].item_url.endswith("/ugs-serving-topics/hazards/hazards_qfaults/hazards_qfaults.json")


def test_rasters_are_the_cdn_cogs_searchable_by_series_and_author():
    (m180,) = cat.rasters(ROOT)
    assert m180.is_raster and m180.href.endswith("/geolmap/cogs/M-180.cog.tif")
    assert m180.matches("personius m-180") and not m180.matches("faults")


def test_metadata_takes_the_catalog_fields_and_the_collection_license():
    props = {"title": "Quaternary Faults", "description": "Fault traces in Utah. Many more words.",
             "keywords": ["faults", "utah"], "ugs:point_of_contact": "UGS"}
    collection = {"license": "CC-BY-4.0", "providers": [{"name": "Utah Geological Survey"}],
                  "links": [{"rel": "license", "href": "https://creativecommons.org/licenses/by/4.0/"}]}
    md = cat.metadata(props, collection, "https://example/item.json")
    assert md == {"title": "Quaternary Faults", "summary": "Fault traces in Utah.",
                  "description": "Fault traces in Utah. Many more words.\n\nSource: https://example/item.json",
                  "tags": "faults, utah", "credits": "Utah Geological Survey",
                  "accessConstraints": "CC-BY-4.0 https://creativecommons.org/licenses/by/4.0/"}
    assert cat.metadata({"title": "x"}) == {"title": "x"}  # nothing invented for what is absent


def test_one_layer_per_value_takes_the_legend_labels_in_order():
    legend = [{"label": "<150 years"}, {"label": "<15,000 years"}]
    assert cat.classes(PER_LAYER, legend) == ("qffhazardunit", [
        ("u150wcqff", "<150 years", (230, 0, 0)), ("u15kwcqff", "<15,000 years", (230, 152, 0))])


def test_a_lookup_table_pairs_legend_labels_by_color():
    legend = [{"label": "High", "color": "#ff0000"}, {"label": "Moderate", "color": "#0070FF"}]
    assert cat.classes(LOOKUP, legend) == ("aafhazardunit", [
        ("Haaf", "High", (255, 0, 0)), ("Maaf", "Moderate", (0, 112, 255))])


def test_a_match_reads_through_downcase_and_expands_value_lists():
    field, got = cat.classes(MATCH)
    assert field == "primsource"
    assert [v for v, _, _ in got] == ["coal", "wind", "solar"]


def test_a_one_color_style_is_not_categorical_but_has_a_color():
    style = {"layers": [{"type": "fill", "paint": {"fill-color": "#0056B3"}}]}
    assert cat.classes(style) is None
    assert cat.single_color(style) == (0, 86, 179)


def _asset(data: bytes) -> dict:
    return {"href": "https://example/x.parquet", "file:size": len(data),
            "file:checksum": "1220" + hashlib.sha256(data).hexdigest()}


def test_a_filtered_first_layer_has_no_single_color():
    style = {"layers": [  # enmin_ccus_geochemistry: one circle layer per datatype, one an `in` filter
        {"type": "circle", "filter": ["==", ["get", "datatype"], "core analysis"],
         "paint": {"circle-color": "#7B1FA2"}},
        {"type": "circle", "filter": ["in", "average", ["get", "datatype"]],
         "paint": {"circle-color": "#AB47BC"}}]}
    assert cat.classes(style) is None
    assert cat.single_color(style) is None


def test_a_geometry_type_filter_still_gives_a_single_color():
    style = {"layers": [{"type": "fill", "filter": ["==", "$type", "Polygon"],
                         "paint": {"fill-color": "#0056b3"}}]}
    assert cat.single_color(style) == (0, 86, 179)


def test_download_verifies_and_reuses_a_matching_copy(monkeypatch, tmp_path):
    data = b"PAR1" * 10
    calls = []

    class Resp:
        def __init__(self, body):
            self.body, self.read_at = body, 0
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            chunk, self.read_at = self.body[self.read_at:self.read_at + n], self.read_at + n
            return chunk

    monkeypatch.setattr(cat.urllib.request, "urlopen", lambda url, timeout=0: calls.append(url) or Resp(data))
    path = cat.download(_asset(data), str(tmp_path), "x.parquet")
    assert Path(path).read_bytes() == data
    cat.download(_asset(data), str(tmp_path), "x.parquet")
    assert len(calls) == 1  # the second call found a verified copy

    bad = {**_asset(data), "file:checksum": "1220" + "0" * 64}
    Path(path).unlink()
    with pytest.raises(OSError):
        cat.download(bad, str(tmp_path), "x.parquet")
    assert not list(tmp_path.iterdir())  # neither the file nor its .part is left behind


# ---------------------------------------------------------------- the .pyt against a fake arcpy

HREF = "https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet"
STYLED_ITEM = {"assets": {"data": {"href": HREF, "file:size": 3}}, "properties": {
    "title": "Quaternary Faults", "ugs:renders": {
    "default": {"style_url": "style", "legend": [{"label": "<150 years"}, {"label": "<15,000 years"}]}}}}


class FakeParameter:
    def __init__(self, displayName="", name="", datatype="", parameterType="", direction="", multiValue=False):
        self.name, self.multiValue = name, multiValue
        self.filter = types.SimpleNamespace(type=None, list=[])
        self.value = None
        self.values = []
        self.altered = False
        self.enabled = True

    @property
    def valueAsText(self):
        return None if self.value is None else str(self.value)

    def setErrorMessage(self, msg):
        self.error = msg

    def setWarningMessage(self, msg):
        self.warning = msg


class FakeLayer:
    def __init__(self, path, field_values, broken=False):
        self.path, self.name, self.isBroken = path, "", broken
        self.dataSource = path
        self._field_values = field_values
        self.metadata = types.SimpleNamespace(isReadOnly=False, saved=False)
        self.metadata.save = lambda: setattr(self.metadata, "saved", True)
        self.symbology = types.SimpleNamespace(renderer=None, updateRenderer=self._update)

    def _update(self, kind):
        entries = [types.SimpleNamespace(values=[[v]], label=v,
                                         symbol=types.SimpleNamespace(color=None))
                   for v in self._field_values]
        entries.append(types.SimpleNamespace(values=[], label="<all other values>",
                                             symbol=types.SimpleNamespace(color=None)))
        self.added_all = False
        self.symbology.renderer = types.SimpleNamespace(
            type=kind, fields=[], groups=[types.SimpleNamespace(items=entries)],
            symbol=types.SimpleNamespace(color=None), addAllValues=self._add_all)

    def _add_all(self):
        self.added_all = True


class FakeExecuteError(Exception):
    """arcpy.ExecuteError's stand-in, distinct so a test can't pass on some other failure."""


class FakeArcpy(types.SimpleNamespace):
    """Records what the toolbox asks for. `refuse` names connection providers that fail."""

    def __init__(self, refuse=(), broken=()):
        self.added, self.removed, self.connections, self.inserted = [], [], [], []
        self.refuse, self.broken = set(refuse), set(broken)
        self.fc = {}
        self.home = tempfile.mkdtemp()
        arcpy = self

        def add(path):
            lyr = FakeLayer(path, ["U150WCQFF", "u15kwcqff", "other"],  # upper case, as stored
                            broken=any(f"ugs_cdn_{p.lower()}" in path for p in arcpy.broken))
            lyr.isRasterLayer = path.endswith(".tif")
            arcpy.added.append(lyr)
            return lyr

        def connect(folder, name, provider, bucket, **kw):
            arcpy.connections.append((provider, bucket, kw))
            if provider in arcpy.refuse:
                raise OSError(f"{provider} refused")
            Path(folder, name + ".acs").write_text("acs")

        class Cursor:
            def __init__(self, fc, fields):
                arcpy.fc["insert_fields"] = fields
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def insertRow(self, row):
                arcpy.inserted.append(row)

        the_map = types.SimpleNamespace(addDataFromPath=add, removeLayer=self.removed.append)
        super().__init__(
            Parameter=FakeParameter, ExecuteError=FakeExecuteError,
            GetInstallInfo=lambda: {"Version": "3.5.2"},
            ListFields=lambda lyr: [types.SimpleNamespace(name="QffHazardUnit")],
            Exists=lambda p: False, SpatialReference=lambda code: f"SR{code}",
            ValidateFieldName=lambda name, ws: (name + "_" if name.lower() in ("date", "select")
                                                else "x" * 64 if name.startswith("long") else name),
            FromWKB=lambda wkb, sr: ("geom", bytes(wkb), sr), CreateUniqueName=lambda n, ws: n,
            mp=types.SimpleNamespace(ArcGISProject=lambda name: types.SimpleNamespace(
                activeMap=the_map, homeFolder=arcpy.home)),
            env=types.SimpleNamespace(scratchFolder="/scratch"),
            da=types.SimpleNamespace(InsertCursor=Cursor),
            management=types.SimpleNamespace(
                CreateCloudStorageConnectionFile=connect, GetCount=lambda lyr: ["7"],
                GetRasterProperties=lambda lyr, prop: types.SimpleNamespace(getOutput=lambda i: "4"),
                CreateFileGDB=lambda folder, name: None,
                CreateFeatureclass=lambda gdb, name, shape, **kw: arcpy.fc.update(shape=shape, **kw),
                AddFields=lambda fc, fields: arcpy.fc.update(fields=fields), Delete=lambda p: None),
        )


def _load(monkeypatch, arcpy):
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    loader = importlib.machinery.SourceFileLoader("ugs_pyt", str(DIR / "UGSWarehouse.pyt"))
    spec = importlib.util.spec_from_loader("ugs_pyt", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    listed, scans = cat.layers(INDEX), cat.rasters(ROOT)  # mod.cat is this module: list, then stub
    monkeypatch.setattr(mod.cat, "layers", lambda index=None: listed)
    monkeypatch.setattr(mod.cat, "rasters", lambda index=None: scans)
    monkeypatch.setattr(mod.cat, "get_json", lambda url: PER_LAYER if url == "style" else STYLED_ITEM)
    mod._cache.clear()
    mod._cache["stale"] = None  # no GitHub check unless a test asks for one
    return mod


def _run(mod, tmp_path, source=None):
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[2].values = ["Quaternary Faults [hazards_qfaults]"]
    params[3].value = source or mod.STREAM
    params[4].value = str(tmp_path)
    log = []
    msgs = types.SimpleNamespace(addMessage=log.append, addWarningMessage=log.append)
    tool.execute(params, msgs)
    return log


def test_theme_narrows_the_layer_list(monkeypatch):
    mod = _load(monkeypatch, FakeArcpy())
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    assert len(params[2].filter.list) == 3  # two vector layers and one raster
    assert params[0].filter.list == ["All", "Energy and Minerals", "Geologic Hazards",
                                     "Scanned Geologic Maps"]
    params[0].value, params[0].altered = "Geologic Hazards", True
    tool.updateParameters(params)
    assert params[2].filter.list == ["Quaternary Faults [hazards_qfaults]"]


def test_a_failed_raster_index_keeps_the_vector_layers_and_warns(monkeypatch):
    mod = _load(monkeypatch, FakeArcpy())

    def broken(index=None):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(mod.cat, "rasters", broken)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    assert params[2].filter.list == ["Power Plants [enmin_powerplants]", "Quaternary Faults [hazards_qfaults]"]
    tool.updateMessages(params)
    assert "scanned geologic maps" in params[2].warning and not hasattr(params[2], "error")


def test_search_matches_title_id_and_keywords_and_keeps_picks(monkeypatch):
    mod = _load(monkeypatch, FakeArcpy())
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[1].value, params[1].altered = "SLIP rate", True
    tool.updateParameters(params)
    assert params[2].filter.list == ["Quaternary Faults [hazards_qfaults]"]
    params[2].values = ["Quaternary Faults [hazards_qfaults]"]
    params[1].value = "power"
    tool.updateParameters(params)
    assert params[2].filter.list == ["Power Plants [enmin_powerplants]", "Quaternary Faults [hazards_qfaults]"]


def test_streams_through_the_web_connection_and_styles_like_the_viewer(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    log = _run(mod, tmp_path)

    assert arcpy.connections == [("WEB", "warehouse", {"end_point": "maps-assets.geology.utah.gov"})]
    (lyr,) = arcpy.added
    assert lyr.path == str(tmp_path / "ugs_cdn_web.acs" / "geoparquet" / "hazards_qfaults" / "hazards_qfaults.parquet")
    assert lyr.name == "Quaternary Faults" and "  Opened online. (WEB)" in log
    assert lyr.metadata.saved and lyr.metadata.title == "Quaternary Faults"
    r = lyr.symbology.renderer
    assert r.type == "UniqueValueRenderer" and r.fields == ["QffHazardUnit"]  # the layer's own casing
    assert lyr.added_all
    got = {e.values[0][0]: (e.label, e.symbol.color) for e in r.groups[0].items if e.values}
    assert got["U150WCQFF"] == ("<150 years", {"RGB": [230, 0, 0, 100]})
    assert got["u15kwcqff"] == ("<15,000 years", {"RGB": [230, 152, 0, 100]})
    assert got["other"] == ("other", None)  # a value the style does not name keeps Pro's default


def test_falls_back_to_anonymous_s3_on_the_cdn(monkeypatch, tmp_path):
    arcpy = FakeArcpy(refuse={"WEB"})
    mod = _load(monkeypatch, arcpy)
    _run(mod, tmp_path)
    provider, bucket, kw = arcpy.connections[-1]
    assert provider == "AMAZON" and kw["end_point"] == "maps-assets.geology.utah.gov"
    assert ["AWS_NO_SIGN_REQUEST", "YES"] in kw["config_options"]
    assert "ugs_cdn_amazon.acs" in arcpy.added[-1].path


def test_copies_to_a_geodatabase_when_pro_cannot_open_the_stream(monkeypatch, tmp_path):
    arcpy = FakeArcpy(refuse={"WEB"}, broken={"AMAZON"})
    mod = _load(monkeypatch, arcpy)
    monkeypatch.setattr(mod.cat, "download", lambda asset, folder, name: f"{folder}/{name}")
    monkeypatch.setattr(mod.cat, "pro_ready", lambda path: path)
    monkeypatch.setattr(mod, "_to_fgdb", lambda parquet, folder, name, messages: f"{folder}/UGS Warehouse.gdb/{name}")
    monkeypatch.setattr(mod.cat, "nested_columns", lambda path: [])
    _run(mod, tmp_path)
    assert len(arcpy.removed) == 1  # the broken streamed layer came off the map
    assert arcpy.added[-1].path == f"{tmp_path}/UGS Warehouse.gdb/hazards_qfaults"


def test_one_failed_pick_does_not_stop_the_others(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    real = mod._add_layer

    def flaky(m, layer, *a):
        if layer.id == "hazards_qfaults":
            raise OSError("checksum mismatch")
        return real(m, layer, *a)

    monkeypatch.setattr(mod, "_add_layer", flaky)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[2].values = ["Quaternary Faults [hazards_qfaults]", "Power Plants [enmin_powerplants]"]
    params[4].value = str(tmp_path)
    log = []
    with pytest.raises(FakeExecuteError, match="1 of 2 layers: Quaternary Faults"):
        tool.execute(params, types.SimpleNamespace(addMessage=log.append, addWarningMessage=log.append))
    assert len(arcpy.added) == 1 and any("checksum mismatch" in line for line in log)


def test_a_pick_no_longer_in_the_catalog_fails_alone(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[2].values = ["Gone [retired_layer]", "Power Plants [enmin_powerplants]"]
    params[4].value = str(tmp_path)
    log = []
    with pytest.raises(FakeExecuteError, match=r"1 of 2 layers: Gone \[retired_layer\]"):
        tool.execute(params, types.SimpleNamespace(addMessage=log.append, addWarningMessage=log.append))
    assert len(arcpy.added) == 1 and any("no longer in the UGS catalog" in line for line in log)


def test_old_pro_copies_instead_of_streaming(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    arcpy.GetInstallInfo = lambda: {"Version": "3.3.1"}
    mod = _load(monkeypatch, arcpy)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    tool.updateMessages(params)
    assert "can't open these layers online" in params[3].warning
    monkeypatch.setattr(mod.cat, "download", lambda asset, folder, name: f"{folder}/{name}")
    monkeypatch.setattr(mod.cat, "pro_ready", lambda path: path)
    monkeypatch.setattr(mod, "_to_fgdb", lambda parquet, folder, name, messages: f"gdb/{name}")
    monkeypatch.setattr(mod.cat, "nested_columns", lambda path: [])
    _run(mod, tmp_path)
    assert arcpy.connections == [] and arcpy.added[-1].path == "gdb/hazards_qfaults"


def test_to_fgdb_writes_fields_rows_and_z(monkeypatch, tmp_path):
    import datetime as dt
    import json
    import struct

    import pyarrow as pa
    import pyarrow.parquet as pq

    point_z = struct.pack("<BIddd", 1, 1001, -111.9, 40.7, 1300.0)  # ISO WKB Point Z
    table = pa.table({"name": ["Wasatch", None], "n": pa.array([1, 2], pa.int64()),
                      "when": pa.array([dt.date(2020, 1, 2), None], pa.date32()),
                      "geom": [point_z, None]})
    geo = {"version": "1.1.0", "primary_column": "geom",
           "columns": {"geom": {"encoding": "WKB", "geometry_types": ["Point Z"]}}}
    path = tmp_path / "x.parquet"
    pq.write_table(table.replace_schema_metadata({b"geo": json.dumps(geo).encode()}), path)

    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    fc = mod._to_fgdb(str(path), str(tmp_path), "x", types.SimpleNamespace(addWarningMessage=print))

    assert fc.endswith("UGS Warehouse.gdb/x")
    assert arcpy.fc["shape"] == "POINT" and arcpy.fc["has_z"] == "ENABLED"
    assert arcpy.fc["fields"] == [["name", "TEXT", "name", 7], ["n", "BIGINTEGER", "n", None],
                                  ["when", "DATEONLY", "when", None]]
    assert arcpy.fc["insert_fields"] == ["SHAPE@", "name", "n", "when"]
    assert arcpy.inserted[0] == [("geom", point_z, "SR4326"), "Wasatch", 1, dt.date(2020, 1, 2)]
    assert arcpy.inserted[1] == [None, None, 2, None]


def test_to_fgdb_validates_names_with_the_gdb_and_warns_on_dropped_columns(monkeypatch, tmp_path):
    import json
    import struct

    import pyarrow as pa
    import pyarrow.parquet as pq

    point = struct.pack("<BIdd", 1, 1, -111.9, 40.7)
    table = pa.table({"date": ["2020-01-02"], "select": ["x"], "blob": pa.array([b"\x00"], pa.binary()),
                      "long_a": [1], "long_b": [2], "geom": [point]})
    geo = {"version": "1.1.0", "primary_column": "geom",
           "columns": {"geom": {"encoding": "WKB", "geometry_types": ["Point"]}}}
    path = tmp_path / "x.parquet"
    pq.write_table(table.replace_schema_metadata({b"geo": json.dumps(geo).encode()}), path)

    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    log = []
    mod._to_fgdb(str(path), str(tmp_path), "x", types.SimpleNamespace(addWarningMessage=log.append))

    assert arcpy.fc["fields"][:2] == [["date_", "TEXT", "date", 10], ["select_", "TEXT", "select", 1]]
    long_a, long_b = "x" * 64, "x" * 62 + "_2"  # validated alike, deduped within 64 characters
    assert arcpy.fc["insert_fields"] == ["SHAPE@", "date_", "select_", long_a, long_b]
    assert arcpy.inserted[0][1:] == ["2020-01-02", "x", 1, 2]
    assert log == ["  Left out columns ArcGIS can't store: blob"]


def test_pro_ready_drops_nested_columns_and_fixes_names(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    bbox = pa.array([{"xmin": 0.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0}])
    table = pa.table({"_publication_date": [1], "objectid": [7], "geom": [b"\x01"], "bbox": bbox})
    geo = {"version": "1.1.0", "primary_column": "geom", "columns": {"geom": {
        "encoding": "WKB", "covering": {"bbox": {"xmin": ["bbox", "xmin"]}}}}}
    src = tmp_path / "t.parquet"
    pq.write_table(table.replace_schema_metadata({b"geo": json.dumps(geo).encode()}), src)

    out = pq.read_table(cat.pro_ready(str(src)))
    assert out.column_names == ["publication_date", "objectid_2", "geom"]
    meta = json.loads(out.schema.metadata[b"geo"])
    assert meta["primary_column"] == "geom" and "covering" not in meta["columns"]["geom"]


@pytest.fixture(autouse=True)
def _no_commit_lookup(monkeypatch):
    monkeypatch.setattr(cat, "_commit", lambda branch, timeout: branch)


def test_update_fetches_by_commit_so_a_push_shows_at_once(monkeypatch):
    seen = []

    class Resp:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return self.body

    def urlopen(req, timeout=0):
        url = getattr(req, "full_url", req)
        seen.append(url)
        if "api.github.com" in url:
            return Resp(b"a" * 40)
        return Resp(b"x = 1\n" + b"#" * 100)

    monkeypatch.undo()  # the real _commit
    monkeypatch.setattr(cat.urllib.request, "urlopen", urlopen)
    cat._fetch_toolbox("feat/x")
    assert seen[0].endswith("/commits/feat/x")
    assert all(f"/{'a' * 40}/arcgis-pro/" in u for u in seen[1:])

    def offline(req, timeout=0):
        if "api.github.com" in getattr(req, "full_url", req):
            raise OSError("rate limited")
        return Resp(b"x = 1\n" + b"#" * 100)

    monkeypatch.setattr(cat.urllib.request, "urlopen", offline)
    assert cat._commit("feat/x", 2) == "feat/x"  # falls back to the branch URL


def test_update_replaces_both_files_only_when_both_are_good(monkeypatch, tmp_path):
    served = {"UGSWarehouse.pyt": b"x = 1\n" + b"#" * 100, "ugs_catalog.py": b"y = 2\n" + b"#" * 100}

    class Resp:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return self.body

    urls = []
    monkeypatch.setattr(cat.urllib.request, "urlopen",
                        lambda url, timeout=0: urls.append(url) or Resp(served[url.rsplit("/", 1)[1]]))
    (tmp_path / "UGSWarehouse.pyt").write_bytes(b"x = 0\n")
    (tmp_path / "ugs_catalog.py").write_bytes(served["ugs_catalog.py"])

    assert cat.update_toolbox(str(tmp_path), "feat/x") == ["UGSWarehouse.pyt"]
    assert urls[0].endswith("/ugs-warehouse/feat/x/arcgis-pro/UGSWarehouse.pyt")
    assert (tmp_path / "UGSWarehouse.pyt").read_bytes() == served["UGSWarehouse.pyt"]
    assert (tmp_path / "UGSWarehouse.pyt.bak").read_bytes() == b"x = 0\n"
    assert cat.update_toolbox(str(tmp_path), "feat/x") == []  # nothing new the second time

    good = served["ugs_catalog.py"]
    served["ugs_catalog.py"] = b"<html>404: Not Found</html>" * 10  # a bad branch, or a cut-off download
    with pytest.raises(SyntaxError):
        cat.update_toolbox(str(tmp_path), "nope")
    served["ugs_catalog.py"] = b""  # empty compiles, so size is checked too
    with pytest.raises(ValueError):
        cat.update_toolbox(str(tmp_path), "nope")
    assert (tmp_path / "ugs_catalog.py").read_bytes() == good  # neither file was touched
    assert cat.toolbox_branch(str(tmp_path)) == "feat/x"  # a failed update does not move the branch


def test_reports_stale_files_and_remembers_the_branch(monkeypatch, tmp_path):
    served = {"UGSWarehouse.pyt": b"x = 1\n" + b"#" * 100, "ugs_catalog.py": b"y = 2\n" + b"#" * 100}

    class Resp:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return self.body

    def urlopen(url, timeout=0):
        if "/offline/" in url:
            raise OSError("no network")
        return Resp(served[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(cat.urllib.request, "urlopen", urlopen)
    (tmp_path / "UGSWarehouse.pyt").write_bytes(b"x = 0\n")
    (tmp_path / "ugs_catalog.py").write_bytes(served["ugs_catalog.py"])
    assert cat.toolbox_branch(str(tmp_path)) == "main"
    assert cat.stale_files(str(tmp_path), "main") == ["UGSWarehouse.pyt"]
    assert cat.stale_files(str(tmp_path), "offline") is None  # no verdict, not a false alarm

    cat.update_toolbox(str(tmp_path), "feat/x")
    assert cat.toolbox_branch(str(tmp_path)) == "feat/x"
    assert cat.stale_files(str(tmp_path), "feat/x") == []


def test_a_crlf_checkout_of_the_same_files_is_not_stale(monkeypatch, tmp_path):
    files = {"UGSWarehouse.pyt": b"a = 1\nb = 2\n", "ugs_catalog.py": b"c = 3\n"}
    for name, body in files.items():
        (tmp_path / name).write_bytes(body.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(cat, "_fetch_toolbox", lambda branch, timeout=60: files)
    assert cat.stale_files(str(tmp_path), "main") == []
    (tmp_path / "ugs_catalog.py").write_bytes(b"c = 4\r\n")
    assert cat.stale_files(str(tmp_path), "main") == ["ugs_catalog.py"]


def test_update_skips_a_crlf_copy_of_the_same_file(monkeypatch, tmp_path):
    files = {"UGSWarehouse.pyt": b"a = 1\n" * 30, "ugs_catalog.py": b"c = 3\n" * 30}
    (tmp_path / "UGSWarehouse.pyt").write_bytes(files["UGSWarehouse.pyt"].replace(b"\n", b"\r\n"))
    (tmp_path / "ugs_catalog.py").write_bytes(b"c = 4\n" * 30)
    monkeypatch.setattr(cat, "_fetch_toolbox", lambda branch, timeout=60: files)
    assert cat.update_toolbox(str(tmp_path), "main") == ["ugs_catalog.py"]
    assert not (tmp_path / "UGSWarehouse.pyt.bak").exists()


def test_the_dialog_warns_when_out_of_date(monkeypatch):
    mod = _load(monkeypatch, FakeArcpy())
    monkeypatch.setattr(mod.cat, "stale_files", lambda folder, branch, timeout=5: ["ugs_catalog.py"])
    mod._cache.pop("stale")
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    tool.updateMessages(params)
    assert "Update Toolbox" in params[0].warning


def test_a_raster_is_added_from_its_cog_with_metadata_and_no_style(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[2].values = ["Geologic map of the Salt Lake City quadrangle [M-180]"]
    params[4].value = str(tmp_path)
    tool.execute(params, types.SimpleNamespace(addMessage=lambda m: None, addWarningMessage=lambda m: None))

    (lyr,) = arcpy.added  # through the CDN connection, its bucket the first path segment
    assert lyr.path == f"{tmp_path}/ugs_cdn_web_geolmap.acs/cogs/M-180.cog.tif"
    assert arcpy.connections == [("WEB", "geolmap", {"end_point": "maps-assets.geology.utah.gov"})]
    assert lyr.symbology.renderer is None
    assert lyr.metadata.summary == "A 1:24,000 map." and lyr.metadata.credits == "Personius"


def test_a_raster_falls_back_to_its_url_and_fails_loudly_when_nothing_opens(monkeypatch, tmp_path):
    arcpy = FakeArcpy(refuse={"WEB"}, broken={"AMAZON"})
    mod = _load(monkeypatch, arcpy)
    url = "https://maps-assets.geology.utah.gov/geolmap/cogs/M-180.cog.tif"
    log = []
    msgs = types.SimpleNamespace(addMessage=log.append)
    lyr = mod._open_raster(arcpy.mp.ArcGISProject("CURRENT").activeMap, url, str(tmp_path), msgs)
    assert lyr.path == url and len(arcpy.removed) == 1  # the broken AMAZON layer came off
    assert log == ["  Opened online. (URL)"]
    arcpy.added.clear()
    mod._open_raster(arcpy.mp.ArcGISProject("CURRENT").activeMap, url, str(tmp_path), msgs)
    assert [x.path for x in arcpy.added] == [url]  # the way that worked is tried first next time

    arcpy.management.GetRasterProperties = lambda lyr, prop: types.SimpleNamespace(getOutput=lambda i: "0")
    with pytest.raises(RuntimeError, match="WEB: WEB refused; AMAZON: the layer is broken; URL: no bands"):
        mod._cache.pop("raster")
        mod._open_raster(arcpy.mp.ArcGISProject("CURRENT").activeMap, url, str(tmp_path), msgs)


def test_cdn_parts_splits_any_cdn_href_at_its_first_segment():
    assert cat.cdn_parts("https://maps-assets.geology.utah.gov/geolmap/cogs/M-180.cog.tif") == \
        ("geolmap", "cogs/M-180.cog.tif")
    assert cat.cdn_parts("https://ugspub.nr.utah.gov/x.tif") is None
    assert cat.cdn_parts("https://maps-assets.geology.utah.gov/x.tif") is None


def test_a_raster_takes_its_description_from_the_full_item(monkeypatch, tmp_path):
    entry = dict(ROOT["items"][0], links=[{"rel": "self", "href": "./ugs-publications/M/M-180/M-180.json"}])
    entry["properties"] = {k: v for k, v in entry["properties"].items() if k != "description"}
    scans = cat.rasters({"items": [entry]})  # before _load stubs rasters()
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    assert scans[0].self_href.endswith("/warehouse/stac/ugs-publications/M/M-180/M-180.json")
    monkeypatch.setattr(mod.cat, "rasters", lambda index=None: scans)
    full = {"properties": {**entry["properties"], "description": "The full map text. More."}}
    monkeypatch.setattr(mod.cat, "get_json", lambda url: full)
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[2].values = ["Geologic map of the Salt Lake City quadrangle [M-180]"]
    params[4].value = str(tmp_path)
    tool.execute(params, types.SimpleNamespace(addMessage=lambda m: None, addWarningMessage=lambda m: None))
    assert arcpy.added[0].metadata.summary == "The full map text."


def test_a_theme_collection_is_read_once_per_session(monkeypatch, tmp_path):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    urls = []

    def get_json(url):
        urls.append(url)
        return PER_LAYER if url == "style" else STYLED_ITEM

    monkeypatch.setattr(mod.cat, "get_json", get_json)
    _run(mod, tmp_path)
    _run(mod, tmp_path)
    assert sum(u.endswith("/hazards/collection.json") for u in urls) == 1


def test_downloads_default_to_the_project_folder(monkeypatch):
    arcpy = FakeArcpy()
    mod = _load(monkeypatch, arcpy)
    params = mod.AddLayer().getParameterInfo()
    assert params[4].value == str(Path(arcpy.home) / "UGS Warehouse")
    assert Path(params[4].value).is_dir()

    def no_project(name):
        raise OSError("no project open")

    monkeypatch.setattr(arcpy.mp, "ArcGISProject", no_project)
    assert mod._default_folder() == "/scratch"


def test_a_pull_request_number_resolves_to_its_head_commit(monkeypatch):
    monkeypatch.undo()  # the real _commit
    monkeypatch.setattr(cat, "get_json", lambda url: {"head": {"sha": "b" * 40}} if url.endswith("/pulls/535") else {})
    assert cat._commit("#535", 2) == "b" * 40

    def gone(url):
        raise OSError("404")

    monkeypatch.setattr(cat, "get_json", gone)
    with pytest.raises(cat.VersionNotFound):
        cat._commit("999", 2)


def test_a_missing_version_is_reported_as_not_found(monkeypatch, tmp_path):
    import urllib.error

    def urlopen(url, timeout=0):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(cat.urllib.request, "urlopen", urlopen)
    with pytest.raises(cat.VersionNotFound):
        cat.update_toolbox(str(tmp_path), "main")


def test_clearing_search_widens_the_list_again_and_no_match_says_so(monkeypatch):
    mod = _load(monkeypatch, FakeArcpy())
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[1].value, params[1].altered = "power", True
    tool.updateParameters(params)
    assert params[2].filter.list == ["Power Plants [enmin_powerplants]"]
    params[1].value, params[1].altered = None, False  # Pro: a cleared box is back at its default
    tool.updateParameters(params)
    assert len(params[2].filter.list) == 3

    params[1].value = "nothing like this"
    tool.updateParameters(params)
    tool.updateMessages(params)
    assert params[2].filter.list == [] and "No layers match" in params[1].warning
