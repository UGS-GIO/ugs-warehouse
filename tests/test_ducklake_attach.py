"""ducklake.attach: the GCS route goes through obstore-fsspec and never loads httpfs (org policy
blocks the HMAC keys httpfs needs), and every value spliced into ATTACH is quoted."""
import fsspec
import obstore.fsspec
import pytest

from ugs_warehouse.vector import ducklake


class FakeCon:
    def __init__(self, attached=False):
        self.sql, self.filesystems, self._attached = [], [], attached

    def execute(self, sql, params=None):
        self.sql.append(sql)
        row = (1,) if self._attached and "duckdb_databases" in sql else None
        return type("R", (), {"fetchone": lambda _self: row})()

    def register_filesystem(self, fs):
        self.filesystems.append(fs)


@pytest.fixture
def gs(monkeypatch):
    monkeypatch.setattr(ducklake, "CATALOG_DSN", "host=/cloudsql/x dbname=db options='-c a=b'")
    monkeypatch.setattr(ducklake, "DATA_PATH", "gs://bucket/warehouse/ducklake/")
    monkeypatch.setattr(obstore.fsspec, "register", lambda proto: None)
    monkeypatch.setattr(fsspec, "filesystem", lambda proto: f"fs:{proto}")
    monkeypatch.delenv("OVERRIDE_DATA_PATH", raising=False)


def test_gcs_path_registers_obstore_and_skips_httpfs(gs):
    con = FakeCon()
    assert ducklake.attach(con) == "warehouse"
    assert con.filesystems == ["fs:gs"]
    assert not any("httpfs" in s for s in con.sql)


def test_attach_quotes_the_dsn_and_pins_the_metadata_schema(gs):
    con = FakeCon()
    ducklake.attach(con)
    attach = con.sql[-1]
    assert "options=''-c a=b''" in attach
    assert "METADATA_SCHEMA 'ducklake_catalog'" in attach
    assert "OVERRIDE_DATA_PATH" not in attach


def test_override_data_path_flag(gs, monkeypatch):
    monkeypatch.setenv("OVERRIDE_DATA_PATH", "True")
    con = FakeCon()
    ducklake.attach(con)
    assert con.sql[-1].endswith(", OVERRIDE_DATA_PATH TRUE)")


def test_local_path_loads_httpfs_and_registers_nothing(gs, monkeypatch):
    monkeypatch.setattr(ducklake, "DATA_PATH", "/tmp/lake/")
    con = FakeCon()
    ducklake.attach(con)
    assert "LOAD httpfs;" in con.sql and con.filesystems == []


def test_already_attached_is_a_no_op(gs):
    con = FakeCon(attached=True)
    assert ducklake.attach(con) == "warehouse"
    assert not any(s.startswith("ATTACH") for s in con.sql)


def test_missing_dsn_stops(monkeypatch):
    monkeypatch.setattr(ducklake, "CATALOG_DSN", "")
    with pytest.raises(SystemExit):
        ducklake.attach(FakeCon())
