"""The shared identifier guard, and the three sites that interpolate names it protects."""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ugs_warehouse.core.identifiers import IDENT_RE, is_identifier, require_identifier
from ugs_warehouse.raster import consume
from ugs_warehouse.raster.identity import Raster
from ugs_warehouse.vector import related, source, transform
from ugs_warehouse.vector.topics import Topic


@pytest.mark.parametrize("value", [
    'x" UNION SELECT 1 --',
    "x$pgq$)) UNION ALL SELECT 99 --",   # `$` is legal in an unquoted PG identifier
    "has space", "has-dash", "a.b", "1leading", "", "x" * 64, None, 123,
])
def test_rejects(value):
    assert not is_identifier(value)
    with pytest.raises(ValueError):
        require_identifier("thing", value)


@pytest.mark.parametrize("value", ["objectid", "_ugs_id", "Shape_Length", "x" * 63])
def test_accepts(value):
    assert is_identifier(value)
    assert require_identifier("thing", value) == value


def test_error_names_the_field():
    with pytest.raises(ValueError, match="related schema"):
        require_identifier("related schema", 'x"')


def test_stream_transformed_rejects_an_unsafe_column(monkeypatch):
    """A column name reaches the same statement the table name does."""
    monkeypatch.setattr(source, "_connect", lambda: MagicMock())
    monkeypatch.setattr(source, "_describe", lambda con, topic: [
        ("objectid", "BIGINT"),
        ("x$pgq$)) UNION ALL SELECT 99 --", "VARCHAR"),
        ("geom", "GEOMETRY"),
    ])
    monkeypatch.setattr(transform, "setup", lambda con: None)

    with pytest.raises(ValueError, match="column"):
        source.stream_transformed(Topic(schema="hazards", layer="hazards_qfaults_current"))


def test_materialize_child_skips_an_unsafe_registry_name(capsys):
    """Best-effort by contract: a bad registry value logs and skips, never sinks the parent."""
    con = MagicMock()
    out = related._materialize_child(
        con, child_topic='evil" --', schema="hazards", display=None,
        rel={}, business_schema={}, parent_stem="hazards_qfaults",
    )
    assert out is None
    con.execute.assert_not_called()
    # Match the reason, not just "SKIP": the broad except above turns any error into that line,
    # so a bare SKIP assertion also passes on a NameError from a missing import.
    logged = capsys.readouterr().out
    assert "SKIP" in logged and "bare SQL identifier" in logged


def test_featureserv_copy_matches_core():
    """`featureserv/gen_db.py` can't import this package — its image installs only that one file
    (featureserv/Dockerfile) — so it carries the pattern inline. Nothing but this test keeps the
    two in step."""
    src = (Path(__file__).resolve().parents[1] / "featureserv" / "gen_db.py").read_text()
    found = re.search(r"^_IDENT_RE = re\.compile\((r\".*\")\)$", src, re.MULTILINE)
    assert found, "featureserv/gen_db.py no longer defines _IDENT_RE at module level"
    assert found.group(1) == f'r"{IDENT_RE.pattern}"', (
        f"featureserv copy {found.group(1)} has drifted from core {IDENT_RE.pattern!r}"
    )


# --- raster: catalog values build GCS object paths and pick the copy source ------------------

@pytest.mark.parametrize("field,value", [
    ("layer", "../../stac"),
    ("layer", "a/b"),
    ("item_id", ".."),
    ("item_id", 'x"'),
    ("collection", "ugs-rasters/../../stac"),
    ("collection", "ugs-rasters//slope"),
])
def test_raster_rejects_traversal(field, value):
    base = dict(layer="slope", item_id="slope_ofr123_20260601",
                collection="ugs-rasters/slope", datetime_iso="2026-06-01T00:00:00Z")
    with pytest.raises(ValueError):
        Raster(**{**base, field: value})


def test_raster_accepts_a_leading_digit_piece_id():
    """Ingest's sanitizer doesn't prefix a leading digit — `30x60 quad` is a real map sheet."""
    r = Raster(layer="geolmap_plates", item_id="30x60_quad_ofr123_20240601",
               collection="ugs-rasters/geolmap_plates", datetime_iso="2024-06-01T00:00:00Z")
    assert r.cog_object_path == "cog/geolmap_plates/30x60_quad_ofr123_20240601.cog.tif"


def test_promote_refuses_a_source_bucket_we_do_not_allow(monkeypatch):
    """The URI is a catalog value, and whatever it names lands in the CDN-served bucket."""
    called = []
    monkeypatch.setattr(consume.gcs, "copy_from_uri", lambda *a, **k: called.append(a))
    with pytest.raises(ValueError, match="not in WAREHOUSE_STAGED_SOURCE_BUCKETS"):
        consume._staged_source("gs://someone-elses-bucket/evil.cog.tif")
    assert not called


def test_promote_accepts_the_allowlisted_bucket():
    uri = "gs://stagedrasters/slope/slope_ofr123_20260601.cog.tif"
    assert consume._staged_source(uri) == uri
