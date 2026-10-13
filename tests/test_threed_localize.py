import pytest

from ugs_warehouse.core import config, gcs
from ugs_warehouse.pubs import threed


def test_localize_streams_a_gs_uri_to_disk(tmp_path):
    data = b"x" * (3 << 20)  # past one stream chunk
    gcs.put_bytes(data, "pubs/M-1.gdb.zip", content_type="application/zip")
    dest = tmp_path / "gdb.zip"
    assert threed._localize(f"gs://{config.BUCKET}/pubs/M-1.gdb.zip", str(dest)) == str(dest)
    assert dest.read_bytes() == data


def test_localize_passes_a_local_path_through(tmp_path):
    assert threed._localize(str(tmp_path), "unused") == str(tmp_path)


def test_a_missing_gs_object_leaves_dest_untouched(tmp_path):
    dest = tmp_path / "gdb.zip"
    with pytest.raises(FileNotFoundError):
        threed._localize(f"gs://{config.BUCKET}/pubs/absent.zip", str(dest))
    assert not dest.exists()
