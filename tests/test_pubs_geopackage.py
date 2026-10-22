"""pubs/vectors.py: the all-layers GeoPackage built from a publication's GeoParquet on GCS."""
from __future__ import annotations

import json
import os

import pytest

from ugs_warehouse.pubs import vectors

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
pyogrio = pytest.importorskip("pyogrio")
box = pytest.importorskip("shapely.geometry").box

SID = "M-228DM"
PREFIX = f"{vectors.VECTORS_PREFIX}/{SID}"
REL = {"origin": "geo__Polys", "origin_cols": ["MapUnit"], "destination": "geo__DMU",
       "destination_cols": ["MapUnit"], "cardinality": "many_to_one", "detected_by": "inferred",
       "confidence": 100}


class Bucket:
    """In-memory GCS: objects by path, plus the calls the GeoPackage build makes."""

    def __init__(self, monkeypatch):
        self.store: dict[str, bytes] = {}
        self.uploads: list[str] = []
        g = vectors.gcs
        monkeypatch.setattr(g, "download", self.download)
        monkeypatch.setattr(g, "upload", self.upload)
        monkeypatch.setattr(g, "get_bytes_versioned", lambda p: (self.store[p], {"v": "1"}))
        monkeypatch.setattr(g, "put_bytes_if_unchanged",
                            lambda data, p, version, **kw: self.store.__setitem__(p, data))
        monkeypatch.setattr(g, "get_file_meta", lambda p: None)
        monkeypatch.setattr(g, "hash_object", lambda p: self.meta(self.store[p]))

    @staticmethod
    def meta(data: bytes):
        import hashlib

        return vectors.gcs.FileMeta(len(data), vectors.gcs.multihash_sha256(hashlib.sha256(data).digest()))

    def download(self, path, dest, **kw):
        with open(dest, "wb") as fh:
            fh.write(self.store[path])

    def upload(self, local, path, **kw):
        with open(local, "rb") as fh:
            self.store[path] = fh.read()
        self.uploads.append(path)
        return self.meta(self.store[path])

    def put_parquet(self, tmp_path, label, frame, spatial):
        local = str(tmp_path / f"{label}.parquet")
        if spatial:
            vectors.geoparquet.write(frame, local)
        else:
            frame.to_parquet(local)
        with open(local, "rb") as fh:
            self.store[f"{PREFIX}/{label}.parquet"] = fh.read()
        meta = self.meta(self.store[f"{PREFIX}/{label}.parquet"])
        return {"label": label, "spatial": spatial, "rows": len(frame), "bytes": meta.size,
                "checksum": meta.checksum}

    def manifest(self, manifest):
        self.store[f"{PREFIX}/_manifest.json"] = json.dumps(manifest).encode()

    def read_manifest(self):
        return json.loads(self.store[f"{PREFIX}/_manifest.json"])


@pytest.fixture
def pub(monkeypatch, tmp_path):
    """A publication with a polygon layer in UTM 12N (three polygons) and a four-row table, joined
    by one relationship, its GeoParquet on an in-memory bucket and its manifest recorded."""
    bucket = Bucket(monkeypatch)
    polys = gpd.GeoDataFrame(
        {"MapUnit": ["Qal", "Tv", "Tv"]},
        geometry=[box(424000, 4512000, 424100, 4512100), box(424100, 4512000, 424200, 4512100),
                  box(424200, 4512000, 424300, 4512100)], crs="EPSG:26912")
    dmu = pd.DataFrame({"MapUnit": ["Qal", "Tv", "Tb", "Tr"], "Name": ["Alluvium", "Volcanics", "b", "r"]})
    layers = [bucket.put_parquet(tmp_path, "geo__Polys", polys, True),
              bucket.put_parquet(tmp_path, "geo__DMU", dmu, False)]
    layers[0].update(pmtiles=True, style="geo__Polys.style.json")
    bucket.manifest({"layers": layers, "spatial": ["geo__Polys"], "tables": ["geo__DMU"],
                     "relationships": [REL], "warnings": [], "errors": [],
                     "relations_version": vectors.relations.RELATIONS_VERSION,
                     "style_version": vectors.layer_style.STYLE_VERSION})
    return bucket


def test_the_geopackage_holds_every_layer_and_table_with_the_relationship(pub, tmp_path):
    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    manifest = pub.read_manifest()
    made = manifest["geopackage"]
    assert made["version"] == vectors.GEOPACKAGE_VERSION
    assert made["bytes"] == len(pub.store[f"{PREFIX}/{SID}.gpkg"]) and made["checksum"]
    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    names = {row[0]: row[1] for row in pyogrio.list_layers(path)}
    assert set(names) == {"geo__Polys", "geo__DMU", vectors.GPKG_RELATIONSHIPS}
    assert names["geo__DMU"] is None  # an attribute table, no geometry
    polys = gpd.read_file(path, layer="geo__Polys")
    assert len(polys) == 3 and polys.crs.to_epsg() == 26912  # its own CRS, not reprojected
    assert sorted(g.bounds[0] for g in polys.geometry) == [424000.0, 424100.0, 424200.0]  # UTM metres
    assert "bbox" not in polys.columns  # the GeoParquet covering column is not a GeoPackage field
    table = pyogrio.read_dataframe(path, layer="geo__DMU", read_geometry=False)
    assert table["MapUnit"].tolist() == ["Qal", "Tv", "Tb", "Tr"]
    rels = pyogrio.read_dataframe(path, layer=vectors.GPKG_RELATIONSHIPS, read_geometry=False)
    assert rels.to_dict("records") == [{
        "origin_table": "geo__Polys", "origin_columns": "MapUnit", "destination_table": "geo__DMU",
        "destination_columns": "MapUnit", "detected_by": "inferred", "confidence": 100}]
    assert pub.uploads == [f"{PREFIX}/{SID}.gpkg"]


def test_layers_are_appended_in_batches_not_read_whole(pub, monkeypatch, tmp_path):
    monkeypatch.setattr(vectors, "GPKG_BATCH_ROWS", 2)
    vectors._geopackage_series(SID)

    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    assert len(gpd.read_file(path, layer="geo__Polys")) == 3
    assert len(pyogrio.read_dataframe(path, layer="geo__DMU", read_geometry=False)) == 4


def test_a_pub_over_the_size_limit_gets_a_warning_and_no_geopackage(pub, monkeypatch):
    monkeypatch.setattr(vectors, "MAX_GPKG_BYTES", 100)

    built, errors = vectors._geopackage_series(SID)

    assert not built and errors == []
    assert f"{PREFIX}/{SID}.gpkg" not in pub.store
    manifest = pub.read_manifest()
    [warning] = [w for w in manifest["warnings"] if w["code"] == vectors.GEOPACKAGE_SKIPPED]
    assert "GiB" in warning["warning"] and "limit" in warning["warning"]
    assert not manifest["geopackage"].get("bytes")  # no asset is published for it
    assert not vectors.needs_geopackage(manifest)  # and it is not tried again every run


@pytest.mark.parametrize("over, built", [(1, False), (0, True), (-1, True)])
def test_the_cap_is_judged_at_the_real_boundary(pub, monkeypatch, over, built):
    """A GeoParquet total just over MAX_GPKG_BYTES is skipped with the warning; at it or under
    it builds. Sizes are the manifest's, so no big files are made."""
    manifest = pub.read_manifest()
    manifest["layers"][0]["bytes"] = vectors.MAX_GPKG_BYTES + over - manifest["layers"][1]["bytes"]
    pub.manifest(manifest)

    vectors._geopackage_series(SID)

    assert (f"{PREFIX}/{SID}.gpkg" in pub.store) is built
    codes = [w["code"] for w in pub.read_manifest()["warnings"]]
    assert (vectors.GEOPACKAGE_SKIPPED in codes) is (not built)


def test_it_rebuilds_only_when_a_layer_file_or_a_relationship_changes(pub, tmp_path):
    vectors._geopackage_series(SID)
    assert not vectors.needs_geopackage(pub.read_manifest())

    built, _ = vectors._geopackage_series(SID)
    assert not built and pub.uploads == [f"{PREFIX}/{SID}.gpkg"]  # nothing changed, nothing uploaded

    # A layer's GeoParquet is rewritten: its recorded checksum no longer matches the manifest's.
    manifest = pub.read_manifest()
    dmu = pd.DataFrame({"MapUnit": ["Qal"], "Name": ["changed"]})
    manifest["layers"][1] = pub.put_parquet(tmp_path, "geo__DMU", dmu, False)
    pub.manifest(manifest)
    assert vectors.needs_geopackage(pub.read_manifest())
    built, errors = vectors._geopackage_series(SID)
    assert built and errors == [] and len(pub.uploads) == 2

    # A relationship change alone is also an input change.
    manifest = pub.read_manifest()
    manifest["relationships"] = []
    pub.manifest(manifest)
    assert vectors.needs_geopackage(pub.read_manifest())


def test_a_version_bump_rebuilds(pub, monkeypatch):
    vectors._geopackage_series(SID)
    monkeypatch.setattr(vectors, "GEOPACKAGE_VERSION", vectors.GEOPACKAGE_VERSION + 1)

    assert vectors.needs_geopackage(pub.read_manifest())
    assert vectors._geopackage_series(SID)[0]


def test_a_manifest_from_before_checksums_were_recorded_is_filled_in_and_packaged(pub):
    manifest = pub.read_manifest()
    for entry in manifest["layers"]:
        del entry["bytes"], entry["checksum"]
    pub.manifest(manifest)
    assert vectors.needs_geopackage(manifest)

    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    done = pub.read_manifest()
    assert all(e["bytes"] and e["checksum"] for e in done["layers"])
    assert not vectors.needs_geopackage(done)


def test_a_layer_that_cannot_be_written_fails_loud_and_publishes_nothing(pub, monkeypatch, capsys):
    real = vectors._append_layer

    def boom(src, out, entry):
        if entry["label"] == "geo__DMU":
            raise ValueError("unsupported column type")
        real(src, out, entry)

    monkeypatch.setattr(vectors, "_append_layer", boom)

    built, errors = vectors._geopackage_series(SID)

    assert not built and len(errors) == 1 and "geo__DMU" in errors[0]
    assert "GeoPackage not built" in capsys.readouterr().err
    assert f"{PREFIX}/{SID}.gpkg" not in pub.store
    manifest = pub.read_manifest()
    assert [w["code"] for w in manifest["warnings"]] == [vectors.GEOPACKAGE_FAILED]
    assert "geopackage" not in manifest
    assert vectors.needs_geopackage(manifest)  # the next heal tries again


def test_validation_layers_and_their_relationships_are_left_out_like_the_catalog(pub, tmp_path):
    manifest = pub.read_manifest()
    bad = pd.DataFrame({"MapUnit": ["Qal"], "Error": ["x"]})
    manifest["layers"].append(pub.put_parquet(tmp_path, "T_1_Errors", bad, False))
    manifest["relationships"].append({**REL, "origin": "T_1_Errors"})
    pub.manifest(manifest)

    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    assert {row[0] for row in pyogrio.list_layers(path)} == {
        "geo__Polys", "geo__DMU", vectors.GPKG_RELATIONSHIPS}
    rels = pyogrio.read_dataframe(path, layer=vectors.GPKG_RELATIONSHIPS, read_geometry=False)
    assert rels["origin_table"].tolist() == ["geo__Polys"]


def test_a_failed_rebuild_keeps_the_last_good_geopackage_and_warns(pub, monkeypatch, tmp_path):
    vectors._geopackage_series(SID)
    good = pub.read_manifest()["geopackage"]
    first_file = pub.store[f"{PREFIX}/{SID}.gpkg"]
    manifest = pub.read_manifest()
    manifest["layers"][1] = pub.put_parquet(tmp_path, "geo__DMU", pd.DataFrame({"MapUnit": ["Z"]}), False)
    pub.manifest(manifest)
    monkeypatch.setattr(vectors, "_append_layer",
                        lambda *a: (_ for _ in ()).throw(ValueError("boom")))

    built, errors = vectors._geopackage_series(SID)

    assert not built and len(errors) == 1 and "boom" in errors[0]
    after = pub.read_manifest()
    assert after["geopackage"] == good  # the asset stays
    assert pub.store[f"{PREFIX}/{SID}.gpkg"] == first_file
    [warning] = [w for w in after["warnings"] if w["code"] == vectors.GEOPACKAGE_STALE]
    assert "boom" in warning["warning"]
    assert vectors.needs_geopackage(after)  # and the next heal tries again


def test_the_heal_packages_a_pub_and_the_ingest_heal_leaves_it_to_the_vectors_job(pub):
    needs = {SID: vectors.needs_of(pub.read_manifest())}
    assert needs[SID].geopackage

    skipped = vectors.self_heal(needs, tiles=False)
    assert skipped.packaged == 0 and f"{PREFIX}/{SID}.gpkg" not in pub.store

    healed = vectors.self_heal(needs)
    assert healed.packaged == 1 and healed.failed == []
    assert "1 GeoPackages built" in healed.summary()
    assert f"{PREFIX}/{SID}.gpkg" in pub.store


def test_extraction_packages_the_pub_and_reports_a_failed_build(pub, monkeypatch):
    # extract_one's tail: a build that raises is listed for the exit code, not lost.
    monkeypatch.setattr(vectors, "_geopackage_series", lambda sid: (_ for _ in ()).throw(OSError("disk full")))
    unpackaged: list[str] = []
    monkeypatch.setattr(vectors, "_record_attempt", lambda *a, **k: None)
    monkeypatch.setattr(vectors, "_fetch_all", lambda work, files: ([("p", None, "geo__Polys", True)], [], {}))
    monkeypatch.setattr(vectors, "_extract_and_upload",
                        lambda *a, **k: {"spatial": ["geo__Polys"], "tables": [], "errors": [],
                                         "warnings": [], "layers": []})
    monkeypatch.setattr(vectors.harvest, "data_files", lambda sid: [vectors.harvest.DataFile("k", "GIS", "u")])

    assert vectors.extract_one(SID, force=True, unpackaged=unpackaged) == "ok"
    assert unpackaged == [f"{SID}: GeoPackage not built: OSError: disk full"]


def test_the_geopackage_object_path():
    assert vectors.geopackage_object(SID) == f"{vectors.VECTORS_PREFIX}/{SID}/{SID}.gpkg"
    assert os.path.basename(vectors.geopackage_object(SID)) == f"{SID}.gpkg"


def test_a_source_column_named_fid_is_kept_as_data(pub, tmp_path):
    frame = pd.DataFrame({"fid": [10, 20], "Name": ["a", "b"]})
    polys = gpd.GeoDataFrame(frame, geometry=[box(0, 0, 1, 1), box(1, 1, 2, 2)], crs="EPSG:26912")
    entries = [pub.put_parquet(tmp_path, "geo__Fid", polys, True),
               pub.put_parquet(tmp_path, "geo__FidTable", frame, False)]
    manifest = pub.read_manifest()
    manifest["layers"] += entries
    pub.manifest(manifest)

    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    assert gpd.read_file(path, layer="geo__Fid")["fid"].tolist() == [10, 20]
    assert pyogrio.read_dataframe(path, layer="geo__FidTable", read_geometry=False)["fid"].tolist() == [10, 20]


def test_a_table_over_the_sqlite_column_limit_is_left_out_with_a_warning(pub, tmp_path):
    wide = pd.DataFrame({f"c{i}": [i] for i in range(2100)})
    entry = pub.put_parquet(tmp_path, "geo__Wide", wide, False)
    entry["columns"] = [{"name": c, "type": "int64"} for c in wide.columns]
    manifest = pub.read_manifest()
    manifest["layers"].append(entry)
    pub.manifest(manifest)

    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    names = {row[0] for row in pyogrio.list_layers(path)}
    assert names == {"geo__Polys", "geo__DMU", vectors.GPKG_RELATIONSHIPS}
    [warning] = [w for w in pub.read_manifest()["warnings"]
                 if w["code"] == vectors.GEOPACKAGE_TABLE_SKIPPED]
    assert "geo__Wide" in warning["warning"] and "2100 columns" in warning["warning"]


def test_relationships_to_a_skipped_table_are_left_out_and_counted(pub, tmp_path):
    wide = pd.DataFrame({f"c{i}": [i] for i in range(2100)})
    entry = pub.put_parquet(tmp_path, "geo__Wide", wide, False)
    entry["columns"] = [{"name": c, "type": "int64"} for c in wide.columns]
    manifest = pub.read_manifest()
    manifest["layers"].append(entry)
    manifest["relationships"] = [
        {"origin": "geo__Polys", "origin_cols": ["a"], "destination": "geo__Wide",
         "destination_cols": ["c0"]},
        {"origin": "geo__Wide", "origin_cols": ["c1"], "destination": "geo__DMU",
         "destination_cols": ["b"]},
        {"origin": "geo__Polys", "origin_cols": ["a"], "destination": "geo__DMU",
         "destination_cols": ["b"]}]
    pub.manifest(manifest)

    built, errors = vectors._geopackage_series(SID)

    assert built and errors == []
    path = tmp_path / "out.gpkg"
    path.write_bytes(pub.store[f"{PREFIX}/{SID}.gpkg"])
    rels = pyogrio.read_dataframe(path, layer=vectors.GPKG_RELATIONSHIPS, read_geometry=False)
    assert rels[["origin_table", "destination_table"]].values.tolist() == [["geo__Polys", "geo__DMU"]]
    [warning] = [w for w in pub.read_manifest()["warnings"]
                 if w["code"] == vectors.GEOPACKAGE_TABLE_SKIPPED]
    assert "2 relationships to it left out" in warning["warning"]


def test_the_ingest_heal_builds_no_geopackage_after_a_relations_change(pub, monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(vectors, "_relations_series", lambda sid: [])
    monkeypatch.setattr(vectors, "_geopackage_series", lambda sid: built.append(sid) or (True, []))
    needs = {SID: vectors.Needs((), False, True, False, False, False)}

    vectors.self_heal(needs, tiles=False)
    assert built == []

    vectors.self_heal(needs)  # the vectors job packages once the relationships are recorded
    assert built == [SID]
