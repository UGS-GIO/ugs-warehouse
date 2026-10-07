"""The ArcGIS Pro toolbox: catalog parsing in `ugs_catalog`, and the `.pyt` against a fake arcpy.

Hermetic: no network, no Pro. The fake arcpy records what the toolbox asks of the map, which is
the part a Windows test cannot show quickly: which field it styles by, and with what colors.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "arcgis-pro"
sys.path.insert(0, str(DIR))
import ugs_catalog as cat  # noqa: E402

INDEX = {"items": [
    {"id": "hazards_qfaults", "properties": {"title": "Quaternary Faults", "ugs:dbt_schema": "hazards"}},
    {"id": "enmin_powerplants", "properties": {"title": "Power Plants", "ugs:dbt_schema": "emp"}},
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


class FakeLayer:
    def __init__(self, path, field_values):
        self.path, self.name = path, ""
        self._field_values = field_values
        self.symbology = types.SimpleNamespace(renderer=None, updateRenderer=self._update)

    def _update(self, kind):
        entries = [types.SimpleNamespace(values=[[v]], label=v,
                                         symbol=types.SimpleNamespace(color=None))
                   for v in self._field_values]
        self.symbology.renderer = types.SimpleNamespace(
            type=kind, fields=[], groups=[types.SimpleNamespace(items=entries)],
            symbol=types.SimpleNamespace(color=None))


@pytest.fixture()
def pyt(monkeypatch):
    added: list[FakeLayer] = []

    def add(path):
        added.append(FakeLayer(path, ["U150WCQFF", "u15kwcqff", "other"]))  # upper case, as stored
        return added[-1]

    the_map = types.SimpleNamespace(addDataFromPath=add)
    arcpy = types.SimpleNamespace(
        Parameter=FakeParameter, ExecuteError=RuntimeError,
        GetInstallInfo=lambda: {"Version": "3.5.2"},
        ListFields=lambda lyr: [types.SimpleNamespace(name="QffHazardUnit")],
        mp=types.SimpleNamespace(ArcGISProject=lambda name: types.SimpleNamespace(activeMap=the_map)),
    )
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    loader = importlib.machinery.SourceFileLoader("ugs_pyt", str(DIR / "UGSWarehouse.pyt"))
    spec = importlib.util.spec_from_loader("ugs_pyt", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    listed = cat.layers(INDEX)  # mod.cat is this same module, so list before stubbing it
    monkeypatch.setattr(mod.cat, "layers", lambda index=None: listed)
    mod._cache.clear()
    return mod, added


def test_theme_narrows_the_layer_list(pyt):
    mod, _ = pyt
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    assert len(params[1].filter.list) == 2
    params[0].value, params[0].altered = "hazards", True
    tool.updateParameters(params)
    assert params[1].filter.list == ["Quaternary Faults [hazards_qfaults]"]


def test_execute_downloads_adds_and_styles_like_the_viewer(pyt, monkeypatch, tmp_path):
    mod, added = pyt
    item = {"assets": {"data": {"href": "h", "file:size": 3}}, "properties": {"ugs:renders": {
        "default": {"style_url": "style", "legend": [{"label": "<150 years"}, {"label": "<15,000 years"}]}}}}
    monkeypatch.setattr(mod.cat, "get_json", lambda url: PER_LAYER if url == "style" else item)
    monkeypatch.setattr(mod.cat, "download", lambda asset, folder, name: f"{folder}/{name}")
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    params[1].values = ["Quaternary Faults [hazards_qfaults]"]
    params[3].value = str(tmp_path)
    msgs = types.SimpleNamespace(addMessage=lambda m: None, addWarningMessage=lambda m: None)

    tool.execute(params, msgs)

    (lyr,) = added
    assert lyr.path == f"{tmp_path}/hazards_qfaults.parquet" and lyr.name == "Quaternary Faults"
    r = lyr.symbology.renderer
    assert r.type == "UniqueValueRenderer" and r.fields == ["QffHazardUnit"]  # the layer's own casing
    got = {e.values[0][0]: (e.label, e.symbol.color) for e in r.groups[0].items}
    assert got["U150WCQFF"] == ("<150 years", {"RGB": [230, 0, 0, 100]})
    assert got["u15kwcqff"] == ("<15,000 years", {"RGB": [230, 152, 0, 100]})
    assert got["other"] == ("other", None)  # a value the style does not name keeps Pro's default


def test_old_pro_is_told_to_use_the_live_source(pyt, monkeypatch):
    mod, _ = pyt
    monkeypatch.setattr(mod.arcpy, "GetInstallInfo", lambda: {"Version": "3.3.1"})
    tool = mod.AddLayer()
    params = tool.getParameterInfo()
    tool.updateMessages(params)
    assert "3.5" in params[2].error
